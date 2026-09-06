import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from courses.models import (
    AssignmentSubmission,
    AssignmentSubmissionAnswer,
    CodingExercise,
    CodingSubmission,
    CodingSubmissionTestResult,
    Enrollment,
    Lecture,
    VideoAsset,
    VideoProcessingJob,
)
from courses.services.assignment_grading import RubricGrader
from courses.services.code_runner import (
    CodeRunner,
    DockerTransientError,
    DockerUnavailableError,
    ScriptTestResult,
)
from courses.services.enrollment_service import recalculate_progress
from courses.transcoding import transcode_video_asset

logger = logging.getLogger(__name__)

# Per-submission stdout/stderr/error_message cap (rolled up from per-test rows).
_SUBMISSION_OUTPUT_CAP = 5000


@shared_task(
    bind=True,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def transcode_video_asset_task(self, video_asset_id: int, job_id: int):
    """Transcode a VideoAsset to HLS renditions.

    Idempotent under retries and redelivery:
      - early-returns if the job is already terminal (completed/failed)
      - acks_late=True so a worker death mid-transcode (e.g. deploy
        force-recreate) causes the broker to redeliver instead of silently
        losing the job.
    """
    logger.info('Starting transcoding task for video_asset=%s job=%s', video_asset_id, job_id)

    video_asset = VideoAsset.objects.select_related('lecture__section__course').get(pk=video_asset_id)
    job = VideoProcessingJob.objects.get(pk=job_id)

    # Short-circuit if already terminal. Without this guard, a redelivered
    # message (acks_late) would re-transcode a completed/failed job.
    if job.status in VideoProcessingJob.TERMINAL_STATUSES:
        logger.info('Job %s already terminal (%s); skipping.', job_id, job.status)
        return {'video_asset_id': video_asset_id, 'job_id': job_id, 'status': job.status, 'skipped': True}

    job.status = VideoProcessingJob.Status.PROCESSING
    job.started_at = job.started_at or timezone.now()
    job.notes = 'Transcoding in progress.'
    job.save(update_fields=['status', 'started_at', 'notes', 'updated_at'])

    video_asset.status = VideoAsset.Status.PROCESSING
    video_asset.save(update_fields=['status', 'updated_at'])

    try:
        master_playlist, renditions, duration_seconds = transcode_video_asset(video_asset)

        with transaction.atomic():
            video_asset.master_playlist = master_playlist
            video_asset.renditions = renditions
            video_asset.duration_seconds = duration_seconds
            video_asset.status = VideoAsset.Status.READY
            video_asset.save(update_fields=['master_playlist', 'renditions', 'duration_seconds', 'status', 'updated_at'])

            lecture = video_asset.lecture
            lecture.stream_master_playlist = master_playlist
            lecture.stream_renditions = renditions
            lecture.transcoding_error = ''
            lecture.save(update_fields=['stream_master_playlist', 'stream_renditions', 'transcoding_error', 'updated_at'])

            job.status = VideoProcessingJob.Status.COMPLETED
            job.completed_at = timezone.now()
            job.notes = 'Transcoding completed successfully.'
            job.save(update_fields=['status', 'completed_at', 'notes', 'updated_at'])

            _lecture_title = video_asset.lecture.title
            _lecture_id = video_asset.lecture.pk
            _course_slug = video_asset.lecture.section.course.slug
            _course_pk = video_asset.lecture.section.course.pk
            _va_id = video_asset_id

            def _notify_video_ready():
                from courses.models import NidusCourse
                from notifications.models import NotificationEventType
                from notifications.services.dispatcher import dispatch
                try:
                    _c = NidusCourse.objects.prefetch_related('instructors').get(pk=_course_pk)
                    dispatch(
                        NotificationEventType.VIDEO_READY,
                        list(_c.instructors.all()),
                        context={
                            'lecture_title': _lecture_title,
                            'lecture_id': _lecture_id,
                            'course_slug': _course_slug,
                        },
                        skip_email=True,
                    )
                except Exception:
                    logger.warning('VIDEO_READY dispatch failed for video_asset=%s', _va_id)

            transaction.on_commit(_notify_video_ready)

        logger.info('Transcoding completed for video_asset=%s', video_asset_id)
        return {'video_asset_id': video_asset_id, 'status': 'completed'}

    except Exception as exc:
        logger.exception('Transcoding failed for video_asset=%s', video_asset_id)

        with transaction.atomic():
            video_asset.status = VideoAsset.Status.FAILED
            video_asset.save(update_fields=['status', 'updated_at'])

            lecture = video_asset.lecture
            lecture.transcoding_error = str(exc)
            lecture.save(update_fields=['transcoding_error', 'updated_at'])

            job.status = VideoProcessingJob.Status.FAILED
            job.completed_at = timezone.now()
            job.notes = f'Transcoding failed: {exc}'
            job.save(update_fields=['status', 'completed_at', 'notes', 'updated_at'])

            if self.request.retries >= self.max_retries:
                _lec_title = video_asset.lecture.title
                _lec_id = video_asset.lecture.pk
                _c_slug = video_asset.lecture.section.course.slug
                _c_pk = video_asset.lecture.section.course.pk
                _va_id_f = video_asset_id

                def _notify_video_failed():
                    from courses.models import NidusCourse
                    from notifications.models import NotificationEventType
                    from notifications.services.dispatcher import dispatch
                    try:
                        _c = NidusCourse.objects.prefetch_related('instructors').get(pk=_c_pk)
                        dispatch(
                            NotificationEventType.VIDEO_FAILED,
                            list(_c.instructors.all()),
                            context={
                                'lecture_title': _lec_title,
                                'lecture_id': _lec_id,
                                'course_slug': _c_slug,
                                'course_id': _c_pk,
                                'video_asset_id': _va_id_f,
                            },
                            skip_email=True,
                        )
                    except Exception:
                        logger.warning('VIDEO_FAILED dispatch failed for video_asset=%s', _va_id_f)

                transaction.on_commit(_notify_video_failed)

        raise


@shared_task(
    bind=True,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def grade_assignment_submission_task(self, submission_id: int):
    """Grade an AssignmentSubmission using its frozen rubric snapshots.

    Idempotent under retries and double-dispatch:
      - early-returns if status is already terminal (passed/failed/grading_failed)
      - acks_late=True so a worker death mid-task causes the broker to
        redeliver; the next invocation either resumes or short-circuits.

    On final failure (retries exhausted), marks status=grading_failed and
    stores a truncated error message on the submission.
    """
    logger.info('Starting grading task for assignment_submission=%s', submission_id)

    submission = AssignmentSubmission.objects.select_related('assignment__section__course').get(
        pk=submission_id,
    )

    # Short-circuit if already terminal. Without this guard, a redelivered
    # message (acks_late) would re-grade a passed/failed submission.
    if submission.status in AssignmentSubmission.TERMINAL_STATUSES:
        logger.info(
            'Submission %s already terminal (%s); skipping.',
            submission_id, submission.status,
        )
        return {'submission_id': submission_id, 'status': submission.status, 'skipped': True}

    submission.status = AssignmentSubmission.Status.GRADING
    submission.save(update_fields=['status', 'updated_at'])

    try:
        with transaction.atomic():
            answers = list(submission.answers.all())
            grader = RubricGrader()
            for answer in answers:
                score, results, feedback = grader.grade(
                    answer.answer_text or '',
                    answer.rubric_snapshot or [],
                    answer.max_score,
                )
                answer.score = score
                answer.criterion_results = results
                answer.feedback = feedback
            if answers:
                AssignmentSubmissionAnswer.objects.bulk_update(
                    answers, ['score', 'criterion_results', 'feedback'],
                )

            submission.total_score = sum(a.score for a in answers)
            submission.graded_at = timezone.now()
            submission.status = (
                AssignmentSubmission.Status.PASSED
                if submission.total_score >= submission.assignment.passing_score
                else AssignmentSubmission.Status.FAILED
            )
            submission.save(update_fields=['total_score', 'graded_at', 'status', 'updated_at'])

            if submission.status == AssignmentSubmission.Status.PASSED:
                course = submission.assignment.section.course
                enrollment = Enrollment.objects.filter(
                    user=submission.user, course=course, is_active=True,
                ).first()
                if enrollment is not None:
                    transaction.on_commit(lambda: recalculate_progress(enrollment))

        logger.info(
            'Grading completed for submission=%s status=%s score=%s/%s',
            submission_id, submission.status, submission.total_score, submission.max_score,
        )
        return {
            'submission_id': submission_id,
            'status': submission.status,
            'score': submission.total_score,
        }

    except Exception as exc:
        logger.exception('Grading raised for submission=%s', submission_id)
        # On final failure, mark grading_failed so the learner can see it
        # and trigger the retry endpoint. autoretry_for will re-raise until
        # then.
        if self.request.retries >= self.max_retries:
            try:
                submission.refresh_from_db(fields=['status'])
                if submission.status not in AssignmentSubmission.TERMINAL_STATUSES:
                    submission.status = AssignmentSubmission.Status.GRADING_FAILED
                    submission.grading_error = str(exc)[:1000]
                    submission.save(update_fields=['status', 'grading_error', 'updated_at'])
            except Exception:
                logger.exception(
                    'Failed to mark submission=%s as grading_failed', submission_id,
                )
            return {'submission_id': submission_id, 'status': 'grading_failed'}
        raise


# ===========================================================================
# Coding exercise execution
# ===========================================================================
#
# Script-based evaluation: the instructor's evaluation_script (on the
# CodingExercise itself) is executed against the learner's code in ONE
# container; each test in the script yields one ScriptTestResult. Two tasks:
#
#   - evaluate_coding_run_task: transient. Returns a plain dict; the Run
#     dispatcher polls AsyncResult and renders it. No DB row, no retries
#     (Run is cheap to re-run from the UI).
#
#   - evaluate_coding_submission_task: persisted. Loads CodingSubmission,
#     runs the suite via CodeRunner, persists CodingSubmissionTestResult
#     rows, updates aggregates (back-filling total_tests — the script
#     decides the count), schedules recalculate_progress on PASS.
#
# acks_late + autoretry_for=DockerTransientError gives us idempotency under
# worker death AND auto-retry of transient daemon hiccups. Learner-code
# failures and image-missing errors are NOT retried — they're terminal
# already and shouldn't burn worker capacity.

def _get_evaluation_script(exercise: CodingExercise) -> str:
    """Return the instructor's evaluation script ('' if unset). The service
    layer guards before dispatch; this is belt-and-braces for tasks that
    raced an instructor edit."""
    return exercise.evaluation_script or ''


def _build_run_result_payload(
    exercise_id: int,
    language: str,
    results: list[ScriptTestResult],
    error_message: str = '',
) -> dict:
    """Shape the Run-task return value into the dict the polling endpoint
    serializes back to the learner. Lives here (not in serializers) because
    Run never touches the DB — the dict IS the payload.
    """
    passed = sum(1 for r in results if r.status == 'passed')
    total = len(results)
    score = round((passed / total) * 100, 2) if total else 0
    if any(r.status == 'error' for r in results):
        status = 'error'
    elif passed == total and total > 0:
        status = 'passed'
    else:
        status = 'failed'
    return {
        'exercise_id': exercise_id,
        'language': language,
        'status': status,
        'total_tests': total,
        'passed_tests': passed,
        'score': score,
        'runtime_ms': sum(r.runtime_ms for r in results),
        'error_message': error_message,
        'test_results': [
            {
                'position': i + 1,
                'test_name': r.test_name,
                'status': r.status,
                'stdout': r.stdout,
                'stderr': r.stderr,
                'runtime_ms': r.runtime_ms,
                'exit_code': 0 if r.status == 'passed' else 1,
            }
            for i, r in enumerate(results)
        ],
    }


def _run_error_payload(exercise_id: int, language: str, error_message: str) -> dict:
    return {
        'exercise_id': exercise_id,
        'language': language,
        'status': 'error',
        'error_message': error_message,
        'total_tests': 0, 'passed_tests': 0, 'score': 0, 'runtime_ms': 0,
        'test_results': [],
    }


@shared_task
def evaluate_coding_run_task(
    exercise_id: int,
    language: str,
    code: str,
    time_limit_ms: int,
    evaluation_script: str | None = None,
):
    """Run-mode evaluation: full suite, no persistence.

    `evaluation_script` overrides the stored script when provided — used by
    the instructor authoring "Run code" / "Run tests" actions so unsaved
    edits (or the synthetic smoke script) can be exercised. Learner runs
    pass None and use the stored script.

    Returns a dict (the Celery result is stored in Redis and expires after
    CELERY_RESULT_EXPIRES). Never raises into Celery FAILURE for a learner
    code error -- those are recorded inside the result dict.
    """
    try:
        exercise = CodingExercise.objects.get(pk=exercise_id)
    except CodingExercise.DoesNotExist:
        return _run_error_payload(exercise_id, language, 'Exercise not found.')

    if evaluation_script is None:
        evaluation_script = _get_evaluation_script(exercise)
    if not evaluation_script.strip():
        return _run_error_payload(
            exercise_id, language,
            'This exercise is missing its evaluation script and cannot be run yet.',
        )

    try:
        results = CodeRunner().run_submission(
            code=code,
            evaluation_script=evaluation_script,
            time_limit_ms=time_limit_ms,
            language=language,
        )
    except DockerUnavailableError as exc:
        logger.error('Run failed (docker unavailable) for exercise=%s: %s', exercise_id, exc)
        return _run_error_payload(
            exercise_id, language,
            'Code execution service unavailable. Please try again.',
        )
    except DockerTransientError as exc:
        logger.warning('Run hit transient docker error for exercise=%s: %s', exercise_id, exc)
        return _run_error_payload(
            exercise_id, language,
            'Transient runner error. Please try again.',
        )

    return _build_run_result_payload(exercise_id, language, results)


@shared_task(
    bind=True,
    acks_late=True,
    autoretry_for=(DockerTransientError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def evaluate_coding_submission_task(self, submission_id: int):
    """Submit-mode evaluation: full suite, persisted, progress on pass.

    Idempotent under acks_late redelivery: short-circuits when the row is
    already terminal. Mirrors grade_assignment_submission_task structurally.
    """
    logger.info('Starting coding submission task for submission=%s', submission_id)

    submission = CodingSubmission.objects.select_related(
        'exercise__section__course',
    ).get(pk=submission_id)

    if submission.status in CodingSubmission.TERMINAL_STATUSES:
        logger.info(
            'CodingSubmission %s already terminal (%s); skipping.',
            submission_id, submission.status,
        )
        return {'submission_id': submission_id, 'status': submission.status, 'skipped': True}

    submission.status = CodingSubmission.Status.GRADING
    submission.save(update_fields=['status', 'updated_at'])

    exercise = submission.exercise
    evaluation_script = _get_evaluation_script(exercise)
    if not evaluation_script.strip():
        _finalize_with_error(
            submission,
            'This exercise is missing its evaluation script and cannot be graded.',
        )
        return {'submission_id': submission_id, 'status': 'error'}

    try:
        try:
            results = CodeRunner().run_submission(
                code=submission.code,
                evaluation_script=evaluation_script,
                time_limit_ms=exercise.time_limit_ms,
                language=submission.language,
            )
        except DockerUnavailableError as exc:
            # Daemon-down is operator action; mark error and DO NOT retry.
            _finalize_with_error(submission, f'Code execution service unavailable: {exc}')
            return {'submission_id': submission_id, 'status': 'error'}

        with transaction.atomic():
            # One row per test the evaluation script ran, in emission order.
            result_rows = [
                CodingSubmissionTestResult(
                    submission=submission,
                    test_name=r.test_name,
                    status=r.status,
                    stdout=r.stdout,
                    stderr=r.stderr,
                    runtime_ms=r.runtime_ms,
                    exit_code=0 if r.status == 'passed' else 1,
                    position=i + 1,
                )
                for i, r in enumerate(results)
            ]
            CodingSubmissionTestResult.objects.bulk_create(result_rows)

            passed = sum(1 for r in results if r.status == 'passed')
            total = len(results)
            score = round((passed / total) * 100, 2) if total else 0
            # Status precedence: ERROR > FAILED > PASSED.
            if any(r.status == 'error' for r in results):
                final_status = CodingSubmission.Status.ERROR
                error_msg = '\n'.join(
                    r.stderr for r in results if r.status == 'error' and r.stderr
                )[:_SUBMISSION_OUTPUT_CAP]
            elif passed == total and total > 0:
                final_status = CodingSubmission.Status.PASSED
                error_msg = ''
            else:
                final_status = CodingSubmission.Status.FAILED
                error_msg = ''

            stdout_concat = '\n'.join(r.stdout for r in results if r.stdout)[:_SUBMISSION_OUTPUT_CAP]
            stderr_concat = '\n'.join(r.stderr for r in results if r.stderr)[:_SUBMISSION_OUTPUT_CAP]

            submission.passed_tests = passed
            submission.total_tests = total
            submission.score = score
            submission.runtime_ms = sum(r.runtime_ms for r in results)
            submission.status = final_status
            submission.error_message = error_msg
            submission.stdout = stdout_concat
            submission.stderr = stderr_concat
            submission.completed_at = timezone.now()
            submission.save(update_fields=[
                'passed_tests', 'total_tests', 'score', 'runtime_ms',
                'status', 'error_message', 'stdout', 'stderr',
                'completed_at', 'updated_at',
            ])

            if final_status == CodingSubmission.Status.PASSED:
                course = exercise.section.course
                enrollment = Enrollment.objects.filter(
                    user=submission.user, course=course, is_active=True,
                ).first()
                if enrollment is not None:
                    transaction.on_commit(lambda: recalculate_progress(enrollment))

        logger.info(
            'Coding submission %s done: status=%s passed=%s/%s',
            submission_id, submission.status, passed, total,
        )
        return {
            'submission_id': submission_id,
            'status': submission.status,
            'passed_tests': passed,
            'total_tests': total,
        }

    except DockerTransientError:
        # Re-raised so autoretry_for picks it up. On final exhaustion the
        # except block below catches and marks error.
        raise
    except Exception as exc:
        logger.exception('Coding submission %s raised', submission_id)
        if self.request.retries >= self.max_retries:
            _finalize_with_error(submission, str(exc))
            return {'submission_id': submission_id, 'status': 'error'}
        raise


def _finalize_with_error(submission: CodingSubmission, message: str):
    """Set ERROR + error_message on a submission, guarding against a row
    that's already terminal (e.g. a concurrent reaper run)."""
    try:
        submission.refresh_from_db(fields=['status'])
        if submission.status in CodingSubmission.TERMINAL_STATUSES:
            return
        submission.status = CodingSubmission.Status.ERROR
        submission.error_message = (message or '')[:_SUBMISSION_OUTPUT_CAP]
        submission.completed_at = timezone.now()
        submission.save(update_fields=[
            'status', 'error_message', 'completed_at', 'updated_at',
        ])
    except Exception:
        logger.exception('Failed to finalize submission=%s as error', submission.pk)


@shared_task
def reap_stuck_coding_submissions_task(stale_minutes: int = 5):
    """Periodically flip CodingSubmissions stuck in queued/grading to error.

    The architecture has no zombie-job recovery otherwise: a worker SIGKILL
    between row creation and the task finishing leaves the submission
    in_flight forever. Polling UIs would hang on it.

    Scheduled via CELERY_BEAT_SCHEDULE in settings.py.
    """
    cutoff = timezone.now() - timedelta(minutes=stale_minutes)
    stuck = CodingSubmission.objects.filter(
        status__in=CodingSubmission.IN_FLIGHT_STATUSES,
        submitted_at__lt=cutoff,
    )
    count = stuck.update(
        status=CodingSubmission.Status.ERROR,
        error_message='Reaped: worker crashed or runner stalled.',
        completed_at=timezone.now(),
    )
    if count:
        logger.warning('Reaped %d stuck coding submission(s).', count)
    return {'reaped': count}


@shared_task
def reap_stuck_video_uploads_task(stale_hours: int = 24):
    """Flip abandoned VideoAsset rows stuck in UPLOADING to FAILED.

    Direct-to-S3 multipart uploads that were initiated but never completed
    leave a VideoAsset in UPLOADING and dangling S3 parts (the bucket
    lifecycle rule aborts the parts after ~7 days; this task just tidies
    the DB side sooner so lecture UIs don't show a permanent "uploading…").
    Runs hourly via CELERY_BEAT_SCHEDULE.
    """
    cutoff = timezone.now() - timedelta(hours=stale_hours)
    count = VideoAsset.objects.filter(
        status=VideoAsset.Status.UPLOADING,
        updated_at__lt=cutoff,
    ).update(status=VideoAsset.Status.FAILED)
    if count:
        logger.warning('reap_stuck_video_uploads_task: reaped %d stuck upload(s).', count)
    return {'reaped': count}


@shared_task
def expire_instructor_invites_task():
    """Mark pending invites past their expiry date as expired. Runs hourly via CELERY_BEAT_SCHEDULE."""
    from courses.models import CourseInstructorInvite

    count = CourseInstructorInvite.objects.filter(
        status=CourseInstructorInvite.STATUS_PENDING,
        expires_at__lt=timezone.now(),
    ).update(status=CourseInstructorInvite.STATUS_EXPIRED)

    if count:
        logger.info('expire_instructor_invites_task: expired %d invite(s).', count)
    return {'expired': count}


@shared_task
def advance_course_schedules_task():
    """Auto-advance course schedules whose dates have passed.

    scheduled → ongoing when start_date is reached; ongoing → completed when
    end_date is reached (null end_date stays ongoing). Per-row transition_to()
    (not bulk update) so validation applies; one bad row never blocks the
    sweep. Runs via CELERY_BEAT_SCHEDULE.
    """
    from courses.models import CourseSchedule

    now = timezone.now()
    started = completed = 0

    due_to_start = CourseSchedule.objects.filter(
        status=CourseSchedule.Status.SCHEDULED,
        start_date__lte=now,
    )
    for schedule in due_to_start:
        try:
            schedule.transition_to(CourseSchedule.Status.ONGOING)
            started += 1
        except Exception:
            logger.exception('Failed to advance schedule %s to ongoing', schedule.pk)

    due_to_end = CourseSchedule.objects.filter(
        status=CourseSchedule.Status.ONGOING,
        end_date__isnull=False,
        end_date__lte=now,
    )
    for schedule in due_to_end:
        try:
            schedule.transition_to(CourseSchedule.Status.COMPLETED)
            completed += 1
        except Exception:
            logger.exception('Failed to advance schedule %s to completed', schedule.pk)

    return {'started': started, 'completed': completed}
