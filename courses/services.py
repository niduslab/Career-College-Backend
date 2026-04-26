import os

from django.db.models import QuerySet

from courses.models import CourseSection, Lecture, NidusCourse, VideoAsset, VideoProcessingJob


def get_publishable_courses() -> QuerySet[NidusCourse]:
    """Return courses currently visible in marketplace listings."""
    return NidusCourse.objects.filter(
        status=NidusCourse.CourseStatus.PUBLISHED,
        is_published=True,
    ).prefetch_related('instructors', 'partner_institutions')


def get_course_sections(course: NidusCourse) -> QuerySet[CourseSection]:
    return CourseSection.objects.filter(course=course).order_by('position', 'id')


def get_section_lectures(section: CourseSection) -> QuerySet[Lecture]:
    return Lecture.objects.filter(section=section).prefetch_related('video_assets').order_by('position', 'id')


def replace_lecture_video_and_enqueue_transcoding(lecture: Lecture, uploaded_file) -> VideoAsset:
    """
    Deactivate previous active assets, create a new active asset, then enqueue transcoding.
    """
    if lecture.content_type != Lecture.ContentType.VIDEO:
        raise ValueError('Video uploads are only allowed for video lectures.')

    VideoAsset.objects.filter(lecture=lecture, is_active=True).update(is_active=False)

    video_asset = VideoAsset.objects.create(
        lecture=lecture,
        video_file=uploaded_file,
        original_filename=getattr(uploaded_file, 'name', '') or '',
        mime_type=getattr(uploaded_file, 'content_type', '') or '',
        file_size=getattr(uploaded_file, 'size', 0) or 0,
        is_active=True,
        status=VideoAsset.Status.UPLOADING,
    )

    job = VideoProcessingJob.objects.create(
        video_asset=video_asset,
        status=VideoProcessingJob.Status.PENDING,
        notes='Video uploaded and queued for transcoding.',
    )

    video_asset.status = VideoAsset.Status.PROCESSING
    video_asset.save(update_fields=['status', 'updated_at'])

    lecture.stream_master_playlist = ''
    lecture.stream_renditions = []
    lecture.transcoding_error = ''
    lecture.save(update_fields=['stream_master_playlist', 'stream_renditions', 'transcoding_error', 'updated_at'])

    from courses.tasks import transcode_video_asset_task

    transcode_video_asset_task.delay(video_asset.id, job.id)
    return video_asset


def get_file_extension(file_path: str) -> str:
    _, ext = os.path.splitext(file_path)
    return ext.lower()
