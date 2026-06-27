import os

from django.contrib.contenttypes.models import ContentType as DjContentType
from django.db import transaction
from django.db.models import F, Max, QuerySet

from courses.models import (
    CourseSection,
    Lecture,
    NidusCourse,
    SectionContent,
    VideoAsset,
    VideoProcessingJob,
)


def get_publishable_courses() -> QuerySet[NidusCourse]:
    """Return courses currently visible in marketplace listings."""
    return NidusCourse.objects.filter(
        status=NidusCourse.CourseStatus.PUBLISHED,
        is_published=True,
    ).select_related('partner_institution').prefetch_related('instructors')


def get_course_sections(course: NidusCourse) -> QuerySet[CourseSection]:
    # select_related joins course_title + author fields to avoid N+1.
    return (
        CourseSection.objects
        .filter(course=course)
        .select_related('course', 'created_by', 'last_edited_by')
        .order_by('position', 'id')
    )


def get_section_lectures(section: CourseSection) -> QuerySet[Lecture]:
    # Order by pk; canonical curriculum order is via SectionContent.
    return (
        Lecture.objects
        .filter(section=section)
        .select_related('created_by', 'last_edited_by')
        .prefetch_related('video_assets')
        .order_by('id')
    )


# ---------------------------------------------------------------------------
# SectionContent helpers
# ---------------------------------------------------------------------------

def get_next_section_content_position(section: CourseSection) -> int:
    result = SectionContent.objects.filter(section=section).aggregate(Max('position'))
    return (result['position__max'] or 0) + 1


def create_section_content_for_object(
    section: CourseSection,
    content_object,
    item_type: str,
    position: int = None,
    created_by=None,
) -> SectionContent:
    if position is None:
        position = get_next_section_content_position(section)
    ct = DjContentType.objects.get_for_model(content_object.__class__)
    return SectionContent.objects.create(
        section=section,
        item_type=item_type,
        content_type=ct,
        object_id=content_object.pk,
        position=position,
        created_by=created_by,
        last_edited_by=created_by,
    )


@transaction.atomic
def reorder_section_content(section_content: SectionContent, new_position: int) -> SectionContent:
    if new_position < 1:
        raise ValueError('position must be a positive integer.')

    section = section_content.section
    # Lock all rows in this section so concurrent reorders cannot interleave.
    section_qs = (
        SectionContent.objects
        .select_for_update()
        .filter(section=section)
    )
    max_position = section_qs.aggregate(Max('position'))['position__max'] or 0
    if max_position == 0:
        return section_content

    # Clamp oversized targets to the last available slot.
    target_position = min(new_position, max_position)
    current_position = section_content.position
    if current_position == target_position:
        return section_content

    # Move the row out of the way to preserve unique(section, position) while shifting.
    temp_position = max_position + 1
    SectionContent.objects.filter(pk=section_content.pk).update(position=temp_position)

    if target_position < current_position:
        # Moving up: shift impacted rows down by 1.
        impacted_ids = list(
            section_qs.filter(
                position__gte=target_position,
                position__lt=current_position,
            ).values_list('id', flat=True)
        )
        impacted_qs = SectionContent.objects.filter(id__in=impacted_ids)
        # Two-phase shift avoids transient unique collisions on backends like SQLite.
        offset = max_position + 1
        impacted_qs.update(position=F('position') + offset)
        impacted_qs.update(position=F('position') - offset + 1)
    else:
        # Moving down: shift impacted rows up by 1.
        impacted_ids = list(
            section_qs.filter(
                position__gt=current_position,
                position__lte=target_position,
            ).values_list('id', flat=True)
        )
        impacted_qs = SectionContent.objects.filter(id__in=impacted_ids)
        # Two-phase shift avoids transient unique collisions on backends like SQLite.
        offset = max_position + 1
        impacted_qs.update(position=F('position') + offset)
        impacted_qs.update(position=F('position') - offset - 1)

    SectionContent.objects.filter(pk=section_content.pk).update(position=target_position)
    section_content.refresh_from_db()
    return section_content


# ---------------------------------------------------------------------------
# Video pipeline
# ---------------------------------------------------------------------------

def replace_lecture_video_and_enqueue_transcoding(lecture: Lecture, uploaded_file) -> VideoAsset:
    """
    Deactivate previous active assets, create a new active asset, then enqueue transcoding.
    """
    if lecture.lecture_type != Lecture.LectureType.VIDEO:
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
