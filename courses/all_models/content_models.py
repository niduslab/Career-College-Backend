import os
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType as DjContentType
from django.core.exceptions import ValidationError
from django.db import models

from core.validators import validate_video_file
from courses.all_models.course_models import AuthoredModel, CourseSection, TimestampedModel


def video_asset_upload_path(instance, filename):
    """
    Store raw lecture videos with a stable, production-grade path:
    courses/{course_slug}/lectures/{lecture_id}/raw/{uuid}.{ext}
    """
    _, ext = os.path.splitext(filename)
    extension = ext.lower() or '.bin'

    course_slug = 'unknown-course'
    lecture_id = 'unknown-lecture'
    if instance.lecture_id:
        lecture_id = str(instance.lecture_id)
        section = getattr(instance.lecture, 'section', None)
        course = getattr(section, 'course', None)
        if course and getattr(course, 'slug', None):
            course_slug = course.slug

    unique_name = uuid.uuid4().hex
    return f"courses/{course_slug}/lectures/{lecture_id}/raw/{unique_name}{extension}"


video_asset_upload_path.__module__ = 'courses.models'


# SectionContent — single ordering layer for all mixed content in a section

class SectionContent(AuthoredModel):
    """
    Ordered slot linking a CourseSection to any content item (Lecture, Quiz, etc.).
    Owns the position/ordering concern so that Lecture, Quiz, and future content
    models stay focused on their own domain and need no position field.
    """

    class ItemType(models.TextChoices):
        LECTURE = 'lecture', 'Lecture'
        QUIZ = 'quiz', 'Quiz'
        ASSIGNMENT = 'assignment', 'Assignment'
        CODING = 'coding', 'Coding Exercise'

    section = models.ForeignKey(
        CourseSection,
        on_delete=models.CASCADE,
        related_name='contents',
    )
    # Denormalized type tag: fast WHERE/filter without a JOIN to django_content_type.
    item_type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
        db_index=True,
        help_text='Discriminator for the type of content object in this slot.',
    )
    # Standard GenericForeignKey trio.
    content_type = models.ForeignKey(
        DjContentType,
        on_delete=models.CASCADE,
        help_text='Django ContentType pointing to the concrete content model.',
    )
    object_id = models.PositiveIntegerField(
        help_text='Primary key of the related content object.',
    )
    content_object = GenericForeignKey('content_type', 'object_id')
    position = models.PositiveIntegerField(default=1, db_index=True)

    class Meta:
        db_table = 'section_contents'
        verbose_name = 'Section Content'
        verbose_name_plural = 'Section Contents'
        ordering = ['section_id', 'position', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['section', 'position'],
                name='uniq_scontent_section_position',
            ),
        ]
        indexes = [
            models.Index(fields=['section', 'position'], name='idx_scontent_section_pos'),
            # Supports reverse GFK lookups: "which slot owns this object?"
            models.Index(fields=['content_type', 'object_id'], name='idx_scontent_ct_object'),
            # Supports curriculum queries filtered by content kind.
            models.Index(fields=['item_type', 'section'], name='idx_scontent_itemtype_section'),
        ]

    def __str__(self):
        return f'Section {self.section_id} @ position {self.position} ({self.item_type})'


# Lecture — ordering delegated to SectionContent

class Lecture(AuthoredModel):
    """Content item inside a section. Supports video and article lectures."""

    class LectureType(models.TextChoices):
        VIDEO = 'video', 'Video'
        ARTICLE = 'article', 'Article'

    section = models.ForeignKey(
        CourseSection,
        on_delete=models.CASCADE,
        related_name='lectures',
    )
    title = models.CharField(max_length=255)
    lecture_type = models.CharField(
        max_length=20,
        choices=LectureType.choices,
        default=LectureType.VIDEO,
        db_index=True,
    )

    # Rich text/markdown/html can be stored here depending on editor strategy.
    article_content = models.TextField(blank=True, default='')
    stream_master_playlist = models.CharField(max_length=500, blank=True, default='')
    stream_renditions = models.JSONField(default=list, blank=True)
    transcoding_error = models.TextField(blank=True, default='')
    is_preview = models.BooleanField(default=False, db_index=True)
    section_content = GenericRelation(
        SectionContent,
        content_type_field='content_type',
        object_id_field='object_id',
        related_query_name='lecture',
    )

    class Meta:
        db_table = 'lectures'
        verbose_name = 'Lecture'
        verbose_name_plural = 'Lectures'
        ordering = ['section_id', 'id']
        constraints = [
            models.CheckConstraint(
                check=(
                    (
                        models.Q(lecture_type='video')
                        & models.Q(article_content='')
                    )
                    | (
                        models.Q(lecture_type='article')
                        & models.Q(article_content__gt='')
                    )
                ),
                name='chk_lecture_payload_by_type',
            ),
        ]
        indexes = [
            models.Index(fields=['lecture_type', 'section'], name='idx_lecture_type_section'),
        ]

    def clean(self):
        super().clean()

        if self.lecture_type == self.LectureType.VIDEO:
            if self.article_content:
                raise ValidationError({'article_content': 'Article content must be empty for video lectures.'})

        if self.lecture_type == self.LectureType.ARTICLE:
            if not self.article_content.strip():
                raise ValidationError({'article_content': 'Article lectures must include content.'})

    def __str__(self):
        return f'{self.section.title} - {self.title}'


class VideoAsset(TimestampedModel):
    """Physical/media representation of a video lecture."""

    class Status(models.TextChoices):
        UPLOADING = 'uploading', 'Uploading'
        PROCESSING = 'processing', 'Processing'
        READY = 'ready', 'Ready'
        FAILED = 'failed', 'Failed'

    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name='video_assets',
    )
    video_file = models.FileField(upload_to=video_asset_upload_path, validators=[validate_video_file])
    original_filename = models.CharField(max_length=255, blank=True, default='')
    mime_type = models.CharField(max_length=100, blank=True, default='')
    file_size = models.BigIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(blank=True, null=True)
    master_playlist = models.CharField(max_length=500, blank=True, default='')
    renditions = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADING,
        db_index=True,
    )

    class Meta:
        db_table = 'video_assets'
        verbose_name = 'Video Asset'
        verbose_name_plural = 'Video Assets'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['lecture'],
                condition=models.Q(is_active=True),
                name='uniq_active_videoasset_per_lecture',
            ),
        ]
        indexes = [
            models.Index(fields=['lecture', 'is_active'], name='idx_vasset_lecture_active'),
            models.Index(fields=['status', '-created_at'], name='idx_vasset_status_date'),
        ]

    def clean(self):
        super().clean()
        if self.file_size < 0:
            raise ValidationError({'file_size': 'File size cannot be negative.'})
        if self.duration_seconds is not None and self.duration_seconds <= 0:
            raise ValidationError({'duration_seconds': 'Duration must be greater than 0 when provided.'})
        if self.lecture_id and self.lecture.lecture_type != Lecture.LectureType.VIDEO:
            raise ValidationError({'lecture': 'Video assets can only be attached to video lectures.'})

    def __str__(self):
        return f'Video for {self.lecture.title} ({self.pk})'


class VideoProcessingJob(TimestampedModel):
    """Asynchronous transcoding/processing jobs for a video asset."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    video_asset = models.ForeignKey(
        VideoAsset,
        on_delete=models.CASCADE,
        related_name='processing_jobs',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    notes = models.TextField(blank=True, default='')
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'video_processing_jobs'
        verbose_name = 'Video Processing Job'
        verbose_name_plural = 'Video Processing Jobs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['video_asset', '-created_at'], name='idx_vjob_asset_date'),
            models.Index(fields=['status', '-created_at'], name='idx_vjob_status_date'),
        ]

    def clean(self):
        super().clean()
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValidationError({'completed_at': 'Completed time cannot be before started time.'})

    def __str__(self):
        return f'Job {self.pk} - {self.status}'

class WatchProgress(TimestampedModel):
    """Per-user progress tracking for lecture playback/completion."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='watch_progress',
    )
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name='watch_progress',
    )
    watched_seconds = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False, db_index=True)
    last_watched_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'watch_progress'
        verbose_name = 'Watch Progress'
        verbose_name_plural = 'Watch Progress'
        ordering = ['-last_watched_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'lecture'], name='uniq_watch_progress_user_lecture'),
        ]
        indexes = [
            models.Index(fields=['user', '-last_watched_at'], name='idx_wprogress_user_last'),
            models.Index(fields=['lecture', 'is_completed'], name='idx_wprogress_lecture_done'),
        ]

    def clean(self):
        super().clean()
        if self.watched_seconds < 0:
            raise ValidationError({'watched_seconds': 'Watched seconds cannot be negative.'})

    def __str__(self):
        return f'{self.user} - {self.lecture} ({self.watched_seconds}s)'
