import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from courses.models import Lecture, VideoAsset, VideoProcessingJob
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
