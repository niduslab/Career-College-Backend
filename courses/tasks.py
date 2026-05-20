import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from courses.models import (
    AssignmentSubmission,
    AssignmentSubmissionAnswer,
    Enrollment,
    Lecture,
    VideoAsset,
    VideoProcessingJob,
)
from courses.services.assignment_grading import RubricGrader
from courses.services.enrollment_service import recalculate_progress
from courses.transcoding import transcode_video_asset

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def transcode_video_asset_task(self, video_asset_id: int, job_id: int):
    logger.info('Starting transcoding task for video_asset=%s job=%s', video_asset_id, job_id)

    video_asset = VideoAsset.objects.select_related('lecture__section__course').get(pk=video_asset_id)
    job = VideoProcessingJob.objects.get(pk=job_id)

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
