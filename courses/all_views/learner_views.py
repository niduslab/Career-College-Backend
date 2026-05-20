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

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified, IsInstructorUser, IsLearnerUser
from courses.all_serializers.learner_serializers import (
    build_assignment_submission_result,
    build_quiz_attempt_result,
)
from courses.models import (
    Assignment,
    AssignmentSubmission,
    Enrollment,
    Lecture,
    NidusCourse,
    Quiz,
)
from courses.serializers import (
    AssignmentSubmissionInputSerializer,
    LearnerAssignmentDetailSerializer,
    LearnerLectureDetailSerializer,
    LearnerQuizDetailSerializer,
    QuizSubmissionSerializer,
    WatchProgressUpsertSerializer,
)
from courses.services import (
    AssignmentSubmissionError,
    get_assignment_for_consumption,
    get_consumption_lecture,
    get_learner_assignment_submission,
    get_quiz_for_consumption,
    load_learner_curriculum,
    resolve_course_access,
    retry_assignment_grading,
    submit_assignment,
    submit_quiz_attempt,
    update_last_accessed,
    upsert_watch_progress,
)

logger = logging.getLogger(__name__)


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

        data = load_learner_curriculum(course, request.user, is_instructor)
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
        ).first()
        if enrollment is None:
            return Response(
                {'success': False, 'message': 'Lecture not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

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
        ).first()
        if enrollment is None:
            return Response(
                {'success': False, 'message': 'Quiz not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

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
        ).first()
        if enrollment is None:
            return Response(
                {'success': False, 'message': 'Assignment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

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
