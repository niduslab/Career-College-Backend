"""
Learner consumption endpoints (Phase 1 + Phase 2).

Routes (all under `/api/v1/courses/`):
    GET  learn/<slug>/curriculum/                                 -> LearnerCurriculumView
    GET  learn/lectures/<int:lecture_id>/                         -> LearnerLectureDetailView
    POST learn/lectures/<int:lecture_id>/progress/                -> LearnerLectureProgressView
    GET  learn/quizzes/<int:quiz_id>/                             -> LearnerQuizDetailView
    POST learn/quizzes/<int:quiz_id>/submit/                      -> LearnerQuizSubmitView
    GET  learn/assignments/<int:assignment_id>/                   -> LearnerAssignmentDetailView
    POST learn/assignments/<int:assignment_id>/submit/            -> LearnerAssignmentSubmitView
    GET  learn/assignments/submissions/<int:submission_id>/       -> LearnerAssignmentSubmissionDetailView
    POST learn/assignments/submissions/<int:submission_id>/retry/ -> LearnerAssignmentSubmissionRetryView

These views deliberately use dedicated learner serializers and a learner
service module so that sensitive instructor-only fields cannot leak.
"""

import logging

from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified, IsInstructorUser, IsLearnerUser
from courses.all_serializers.learner_serializers import (
    build_assignment_submission_result,
    build_coding_run_result_payload,
    build_quiz_attempt_result,
)
from courses.models import (
    Assignment,
    AssignmentSubmission,
    CodingExercise,
    CodingSubmission,
    Enrollment,
    Lecture,
    NidusCourse,
    Quiz,
)
from courses.serializers import (
    AssignmentSubmissionInputSerializer,
    CodingRunSubmitSerializer,
    LearnerAssignmentDetailSerializer,
    LearnerCodingExerciseDetailSerializer,
    LearnerCodingSubmissionSerializer,
    LearnerLectureDetailSerializer,
    LearnerQuizDetailSerializer,
    QuizSubmissionSerializer,
    WatchProgressUpsertSerializer,
)
from courses.services import (
    AssignmentSubmissionError,
    CodingSubmissionError,
    ContentNotReleasedError,
    assert_content_released,
    get_assignment_for_consumption,
    get_coding_exercise_for_consumption,
    get_consumption_lecture,
    get_learner_assignment_submission,
    get_learner_coding_submission,
    get_quiz_for_consumption,
    load_learner_curriculum,
    resolve_course_access,
    retry_assignment_grading,
    retry_coding_submission,
    run_coding_exercise,
    submit_assignment,
    submit_coding_exercise,
    submit_quiz_attempt,
    update_last_accessed,
    upsert_watch_progress,
)

logger = logging.getLogger(__name__)


def _not_released_response(exc):
    """422 for release-timeline violations (cohort pre-start / drip lock)."""
    return Response(
        {'success': False, 'message': exc.message},
        status=exc.http_status,
    )


class LearnerCurriculumView(APIView):
    """
    GET /api/v1/courses/learn/{slug}/curriculum/

    Lightweight ordered curriculum outline for an enrolled learner (or the
    course's own instructor previewing as a learner). Item rows include
    title + type + duration + per-lecture completion marker; heavier item
    payloads come from the per-item detail endpoints.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser | IsInstructorUser]

    def get(self, request, slug):
        course = get_object_or_404(
            NidusCourse.objects.prefetch_related('instructors'),
            slug=slug,
        )

        is_instructor, enrollment = resolve_course_access(request.user, course)
        if not is_instructor and enrollment is None:
            return Response(
                {'success': False, 'message': 'You do not have access to this course.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if enrollment is not None:
            update_last_accessed(enrollment)

        data = load_learner_curriculum(course, request.user, is_instructor, enrollment=enrollment)
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)


class LearnerLectureDetailView(APIView):
    """
    GET /api/v1/courses/learn/lectures/{lecture_id}/

    Learner-safe lecture payload. For video lectures, exposes HLS playlist
    and renditions; for article lectures, exposes article text. Includes the
    caller's WatchProgress so the player can resume.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser | IsInstructorUser]

    def get(self, request, lecture_id):
        try:
            lecture, _course, _is_instructor, watch_progress = get_consumption_lecture(
                request.user, lecture_id,
            )
        except Lecture.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        duration_seconds = None
        active_asset = lecture.video_assets.filter(is_active=True).order_by('-created_at').first()
        if active_asset is not None:
            duration_seconds = active_asset.duration_seconds

        serializer = LearnerLectureDetailSerializer(
            lecture,
            context={
                'duration_seconds': duration_seconds,
                'watch_progress': watch_progress,
            },
        )
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)


class LearnerLectureProgressView(APIView):
    """
    POST /api/v1/courses/learn/lectures/{lecture_id}/progress/

    Idempotent upsert of WatchProgress for the calling learner. Restricted
    to learners with an active enrollment for the lecture's course; the
    enrollment-progress recalc is handled by the WatchProgress post_save
    signal.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, lecture_id):
        lecture = (
            Lecture.objects
            .select_related('section__course')
            .filter(pk=lecture_id)
            .first()
        )
        if lecture is None:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Existence is not leaked: a learner who has no enrollment for the
        # course gets the same 404 a non-existent lecture would produce.
        enrollment = Enrollment.objects.filter(
            user=request.user,
            course=lecture.section.course,
            is_active=True,
        ).select_related('schedule').order_by(F('schedule_id').asc(nulls_first=True)).first()
        if enrollment is None:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            assert_content_released(enrollment, lecture.section)
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        serializer = WatchProgressUpsertSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            wp = upsert_watch_progress(
                user=request.user,
                lecture=lecture,
                watched_seconds=serializer.validated_data['watched_seconds'],
                is_completed=serializer.validated_data['is_completed'],
            )
        except Exception as e:
            logger.error(
                'WatchProgress upsert failed for user=%s lecture=%s: %s',
                request.user.id, lecture.id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        update_last_accessed(enrollment)

        return Response(
            {
                'success': True,
                'message': 'Progress saved.',
                'data': {
                    'lecture_id': lecture.id,
                    'watched_seconds': wp.watched_seconds,
                    'is_completed': wp.is_completed,
                    'last_watched_at': wp.last_watched_at,
                },
            },
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Phase-2 quiz consumption + submission
# =============================================================================

class LearnerQuizDetailView(APIView):
    """
    GET /api/v1/courses/learn/quizzes/{quiz_id}/

    Learner-safe quiz payload for the attempt UI: quiz metadata + ordered
    questions + their answer options (without the `is_correct` flag).
    Includes a `latest_attempt` summary so the frontend can show "you
    scored X/Y last time" before the learner starts a new attempt.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser | IsInstructorUser]

    def get(self, request, quiz_id):
        try:
            quiz, _course, _is_instructor, latest_attempt = get_quiz_for_consumption(
                request.user, quiz_id,
            )
        except Quiz.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Quiz not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        serializer = LearnerQuizDetailSerializer(
            quiz, context={'latest_attempt': latest_attempt},
        )
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)


class LearnerQuizSubmitView(APIView):
    """
    POST /api/v1/courses/learn/quizzes/{quiz_id}/submit/

    Idempotent in the sense that a repeated POST creates a new attempt
    row rather than mutating an old one — historical attempts remain
    intact for analytics / future "best score" policies.

    Restricted to learners with an active enrollment for the quiz's course;
    instructors get 403 (preview must not pollute attempt history).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, quiz_id):
        quiz = (
            Quiz.objects
            .select_related('section__course')
            .prefetch_related('questions__answers')
            .filter(pk=quiz_id)
            .first()
        )
        if quiz is None:
            return Response(
                {'success': False, 'message': 'Quiz not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Existence is not leaked: a learner with no active enrollment for
        # the course gets the same 404 a non-existent quiz would produce.
        enrollment = Enrollment.objects.filter(
            user=request.user,
            course=quiz.section.course,
            is_active=True,
        ).select_related('schedule').order_by(F('schedule_id').asc(nulls_first=True)).first()
        if enrollment is None:
            return Response(
                {'success': False, 'message': 'Quiz not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            assert_content_released(enrollment, quiz.section)
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        serializer = QuizSubmissionSerializer(data=request.data, context={'quiz': quiz})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            attempt = submit_quiz_attempt(
                user=request.user,
                quiz=quiz,
                answers_payload=serializer.validated_data['answers'],
                enrollment=enrollment,
            )
        except Exception as e:
            logger.error(
                'Quiz submission failed for user=%s quiz=%s: %s',
                request.user.id, quiz.id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        update_last_accessed(enrollment)
        return Response(
            {
                'success': True,
                'message': 'Quiz submitted.',
                'data': build_quiz_attempt_result(attempt),
            },
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Phase-2 assignment consumption + submission
# =============================================================================

class LearnerAssignmentDetailView(APIView):
    """
    GET /api/v1/courses/learn/assignments/{assignment_id}/

    Learner-safe assignment payload for the attempt UI: assignment metadata +
    ordered questions (no `model_answer`, no `rubric`) + a `latest_submission`
    summary so the frontend can show the prior attempt's status before the
    learner starts a new attempt.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser | IsInstructorUser]

    def get(self, request, assignment_id):
        try:
            assignment, _course, _is_instructor, latest_submission = get_assignment_for_consumption(
                request.user, assignment_id,
            )
        except Assignment.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Assignment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        serializer = LearnerAssignmentDetailSerializer(
            assignment, context={'latest_submission': latest_submission},
        )
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)


class LearnerAssignmentSubmitView(APIView):
    """
    POST /api/v1/courses/learn/assignments/{assignment_id}/submit/

    Creates a new AssignmentSubmission + per-question answer rows and
    enqueues the grading task. Returns 202 with the submission id and the
    initial status ('submitted'); the learner polls the detail endpoint
    until the status transitions to a terminal value.

    Restricted to learners with an active enrollment for the assignment's
    course; instructors get 403 (preview must not pollute submission history).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, assignment_id):
        assignment = (
            Assignment.objects
            .select_related('section__course')
            .prefetch_related('questions')
            .filter(pk=assignment_id)
            .first()
        )
        if assignment is None:
            return Response(
                {'success': False, 'message': 'Assignment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Existence is not leaked: a learner with no active enrollment for
        # the course gets the same 404 a non-existent assignment would produce.
        enrollment = Enrollment.objects.filter(
            user=request.user,
            course=assignment.section.course,
            is_active=True,
        ).select_related('schedule').order_by(F('schedule_id').asc(nulls_first=True)).first()
        if enrollment is None:
            return Response(
                {'success': False, 'message': 'Assignment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            assert_content_released(enrollment, assignment.section)
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        serializer = AssignmentSubmissionInputSerializer(
            data=request.data, context={'assignment': assignment},
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            submission = submit_assignment(
                user=request.user,
                assignment=assignment,
                answers_payload=serializer.validated_data['answers'],
                enrollment=enrollment,
            )
        except AssignmentSubmissionError as e:
            return Response(
                {'success': False, 'message': e.message},
                status=e.http_status,
            )
        except Exception as e:
            logger.error(
                'Assignment submission failed for user=%s assignment=%s: %s',
                request.user.id, assignment.id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        update_last_accessed(enrollment)
        return Response(
            {
                'success': True,
                'message': 'Assignment submitted. Grading is in progress.',
                'data': {
                    'submission_id': submission.id,
                    'assignment_id': assignment.id,
                    'status': submission.status,
                    'submitted_at': submission.submitted_at,
                    'max_score': submission.max_score,
                },
            },
            status=status.HTTP_202_ACCEPTED,
        )


class LearnerAssignmentSubmissionDetailView(APIView):
    """
    GET /api/v1/courses/learn/assignments/submissions/{submission_id}/

    Returns the learner's own submission with per-question scores,
    criterion-level results, and feedback. The instructor's `model_answer`
    is included on each question only once the submission has reached a
    terminal graded state (`passed` or `failed`).

    Other learners' submissions return 404 — never 403 — so submission
    existence isn't leaked.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request, submission_id):
        try:
            submission = get_learner_assignment_submission(request.user, submission_id)
        except AssignmentSubmission.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Submission not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {'success': True, 'data': build_assignment_submission_result(submission)},
            status=status.HTTP_200_OK,
        )


class LearnerAssignmentSubmissionRetryView(APIView):
    """
    POST /api/v1/courses/learn/assignments/submissions/{submission_id}/retry/

    Re-enqueue grading for a submission stuck in `grading_failed`. The
    same submission row is reused (resets status to `grading` and clears
    `grading_error`) so `submitted_at` and historical correlation stay
    correct.

    Any other current status returns 422; another learner's submission
    returns 404.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, submission_id):
        try:
            submission = retry_assignment_grading(request.user, submission_id)
        except AssignmentSubmission.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Submission not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except AssignmentSubmissionError as e:
            return Response(
                {'success': False, 'message': e.message},
                status=e.http_status,
            )
        except Exception as e:
            logger.error(
                'Assignment retry failed for user=%s submission=%s: %s',
                request.user.id, submission_id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Grading re-enqueued.',
                'data': {
                    'submission_id': submission.id,
                    'status': submission.status,
                },
            },
            status=status.HTTP_202_ACCEPTED,
        )


# ===========================================================================
# Coding exercise consumption (Phase 2)
# ===========================================================================
#
# Six endpoints power the IDE round-trip:
#
#   GET  learn/coding-exercises/<id>/                    -> detail (starter code + problem)
#   POST learn/coding-exercises/<id>/run/                -> dispatch Run (transient)
#   POST learn/coding-exercises/<id>/submit/             -> persist + dispatch Submit
#   GET  learn/coding-exercises/tasks/<task_id>/         -> poll Run result
#   GET  learn/coding-exercises/submissions/<id>/        -> poll Submit result
#   POST learn/coding-exercises/submissions/<id>/retry/  -> retry an errored submission
#
# Run and Submit both execute the instructor's evaluation script against the
# learner's code and both return HTTP 202; the frontend polls one of the two
# GET endpoints. Run is cheap and transient; Submit is persisted and can
# mark progress.


class LearnerCodingExerciseDetailView(APIView):
    """
    GET /api/v1/courses/learn/coding-exercises/{exercise_id}/

    Learner-safe detail for a coding exercise. Returns starter code per
    language (NOT solution_code / evaluation_script) and a summary of the
    caller's latest submission (so the UI can light up "solved").
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser | IsInstructorUser]

    def get(self, request, exercise_id):
        try:
            exercise, _course, _is_instructor, latest = get_coding_exercise_for_consumption(
                request.user, exercise_id,
            )
        except CodingExercise.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Coding exercise not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        serializer = LearnerCodingExerciseDetailSerializer(
            exercise,
            context={'latest_submission': latest},
        )
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK,
        )


class LearnerCodingRunView(APIView):
    """
    POST /api/v1/courses/learn/coding-exercises/{exercise_id}/run/

    Transient: dispatches a Celery task that runs the instructor's
    evaluation script against the submitted code, returning a task_id for
    the frontend to poll. No DB row is created. Instructors are gated out
    so preview clicks can't pollute history (Run history isn't persisted,
    but the contract is symmetric with Submit).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, exercise_id):
        try:
            exercise, _course, _is_instructor, _latest = get_coding_exercise_for_consumption(
                request.user, exercise_id,
            )
        except CodingExercise.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Coding exercise not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        input_serializer = CodingRunSubmitSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': input_serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            task_id = run_coding_exercise(
                request.user,
                exercise,
                input_serializer.validated_data['language'],
                input_serializer.validated_data['code'],
            )
        except CodingSubmissionError as e:
            return Response(
                {'success': False, 'message': e.message},
                status=e.http_status,
            )
        except Exception as e:
            logger.error(
                'Coding Run dispatch failed for user=%s exercise=%s: %s',
                request.user.id, exercise_id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Run dispatched.',
                'data': {'task_id': task_id},
            },
            status=status.HTTP_202_ACCEPTED,
        )


class LearnerCodingSubmitView(APIView):
    """
    POST /api/v1/courses/learn/coding-exercises/{exercise_id}/submit/

    Persisted: creates a CodingSubmission(queued) and enqueues the
    evaluation task. Returns the submission row (with status='queued')
    so the frontend immediately has an ID to poll on submissions/<id>/.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, exercise_id):
        try:
            exercise, _course, _is_instructor, _latest = get_coding_exercise_for_consumption(
                request.user, exercise_id,
            )
        except CodingExercise.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Coding exercise not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ContentNotReleasedError as exc:
            return _not_released_response(exc)

        input_serializer = CodingRunSubmitSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': input_serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pull the active enrollment so the task can recalc progress on PASS
        # without an extra query. resolve_course_access already validated
        # access inside the consumption loader; we re-fetch here because the
        # learner role guarantees enrollment is the only valid path.
        enrollment = Enrollment.objects.filter(
            user=request.user, course=exercise.section.course, is_active=True,
        ).first()

        try:
            submission = submit_coding_exercise(
                request.user,
                exercise,
                input_serializer.validated_data['language'],
                input_serializer.validated_data['code'],
                enrollment=enrollment,
            )
        except CodingSubmissionError as e:
            return Response(
                {'success': False, 'message': e.message},
                status=e.http_status,
            )
        except Exception as e:
            logger.error(
                'Coding Submit failed for user=%s exercise=%s: %s',
                request.user.id, exercise_id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Submission queued.',
                'data': LearnerCodingSubmissionSerializer(submission).data,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class LearnerCodingTaskStatusView(APIView):
    """
    GET /api/v1/courses/learn/coding-exercises/tasks/{task_id}/

    Polls the Celery result backend for a Run-mode task. States:
      PENDING / STARTED -> 200 {state}
      SUCCESS           -> 200 {state, result}
      FAILURE           -> 500 {state, error}

    Task IDs are unguessable UUIDs and the result payload is learner-safe
    by construction (test names + outputs, never the evaluation script).
    We don't re-validate user ownership of the task_id; coupled with the
    UUID this matches the source standalone platform's contract.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request, task_id):
        # Local import to keep this module importable without celery in
        # environments where the broker isn't reachable.
        from celery.result import AsyncResult

        async_result = AsyncResult(task_id)
        state = async_result.state

        if state == 'PENDING':
            return Response(
                {'success': True, 'data': {'state': 'PENDING'}},
                status=status.HTTP_200_OK,
            )
        if state == 'STARTED':
            return Response(
                {'success': True, 'data': {'state': 'STARTED'}},
                status=status.HTTP_200_OK,
            )
        if state == 'SUCCESS':
            try:
                result = async_result.get(timeout=1)
            except Exception as e:
                logger.error('Failed to read Run task result %s: %s', task_id, e)
                return Response(
                    {
                        'success': False,
                        'message': 'Run result unavailable.',
                        'data': {'state': 'FAILURE', 'error': str(e)},
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            return Response(
                {
                    'success': True,
                    'data': {
                        'state': 'SUCCESS',
                        'result': build_coding_run_result_payload(result),
                    },
                },
                status=status.HTTP_200_OK,
            )
        # FAILURE or REVOKED.
        error_msg = ''
        try:
            error_msg = str(async_result.result) if async_result.result else 'Task failed.'
        except Exception:
            error_msg = 'Task failed.'
        return Response(
            {
                'success': False,
                'message': 'Run failed.',
                'data': {'state': state, 'error': error_msg},
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class LearnerCodingSubmissionDetailView(APIView):
    """
    GET /api/v1/courses/learn/coding-exercises/submissions/{submission_id}/

    Full Submit-mode submission for the calling learner: one result row per
    test the instructor's evaluation script ran (name, status, output,
    failure message).

    Other learners' submissions return 404 (never 403) so existence
    isn't leaked across IDs.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request, submission_id):
        try:
            submission = get_learner_coding_submission(request.user, submission_id)
        except CodingSubmission.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Submission not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {'success': True, 'data': LearnerCodingSubmissionSerializer(submission).data},
            status=status.HTTP_200_OK,
        )


class LearnerCodingSubmissionRetryView(APIView):
    """
    POST /api/v1/courses/learn/coding-exercises/submissions/{submission_id}/retry/

    Re-enqueue evaluation for a submission stuck in ERROR. Reuses the
    same row so submitted_at and per-test history correlate. Any other
    status -> 422; another learner's submission -> 404.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, submission_id):
        try:
            submission = retry_coding_submission(request.user, submission_id)
        except CodingSubmission.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Submission not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except CodingSubmissionError as e:
            return Response(
                {'success': False, 'message': e.message},
                status=e.http_status,
            )
        except Exception as e:
            logger.error(
                'Coding retry failed for user=%s submission=%s: %s',
                request.user.id, submission_id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Submission re-enqueued.',
                'data': {
                    'submission_id': submission.id,
                    'status': submission.status,
                },
            },
            status=status.HTTP_202_ACCEPTED,
        )
