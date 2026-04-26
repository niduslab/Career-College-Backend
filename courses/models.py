import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from auth.models import PartnerInstitutionProfile


def course_thumbnail_upload_path(instance, filename):
    """Generate deterministic, URL-safe upload path for course thumbnails."""
    base_name, ext = os.path.splitext(filename)
    slug = slugify(base_name) or 'thumbnail'
    unique_suffix = uuid.uuid4().hex[:10]
    return f"courses/thumbnails/{slug}_{unique_suffix}{ext.lower()}"


def video_asset_upload_path(instance, filename):
    """
    Store raw lecture videos with a stable, production-grade path:
    media/courses/{course_slug}/lectures/{lecture_id}/raw/{uuid}.{ext}
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
    return f"media/courses/{course_slug}/lectures/{lecture_id}/raw/{unique_name}{extension}"


class TimestampedModel(models.Model):
    """Reusable timestamp fields for operational models."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CourseCategory(models.Model):
    """Taxonomy for organizing courses into marketplace categories."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, db_index=True)
    description = models.TextField(blank=True, default='')
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children',
    )
    is_active = models.BooleanField(default=True, db_index=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'course_categories'
        verbose_name = 'Course Category'
        verbose_name_plural = 'Course Categories'
        ordering = ['display_order', 'name']
        indexes = [
            models.Index(fields=['is_active', 'display_order'], name='idx_ccat_active_order'),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or 'category'
            candidate = base_slug
            suffix = 1
            while CourseCategory.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class NidusCourse(models.Model):
    """Core course entity for a marketplace experience similar to Udemy."""

    class CourseStatus(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        UNDER_REVIEW = 'under_review', 'Under Review'
        PUBLISHED = 'published', 'Published'
        REJECTED = 'rejected', 'Rejected'
        ARCHIVED = 'archived', 'Archived'

    class CourseLevel(models.TextChoices):
        BEGINNER = 'beginner', 'Beginner'
        INTERMEDIATE = 'intermediate', 'Intermediate'
        ADVANCED = 'advanced', 'Advanced'

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_nidus_courses',
        help_text='Instructor who initiated the course.',
    )
    instructors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='instructed_nidus_courses',
        blank=True,
        help_text='One course can have one or many instructors.',
    )
    partner_institutions = models.ManyToManyField(
        PartnerInstitutionProfile,
        related_name='nidus_courses',
        blank=True,
        help_text='Partner institutions associated with this course.',
    )
    category = models.ForeignKey(
        CourseCategory,
        on_delete=models.SET_NULL,
        related_name='courses',
        blank=True,
        null=True,
        db_index=True,
    )

    title = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=280, unique=True, db_index=True)
    description = models.TextField()
    thumbnail = models.ImageField(upload_to=course_thumbnail_upload_path, blank=True, null=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Course list price in marketplace currency.',
    )
    language = models.CharField(max_length=50, default='English', db_index=True)
    level = models.CharField(
        max_length=20,
        choices=CourseLevel.choices,
        default=CourseLevel.BEGINNER,
        db_index=True,
    )
    duration_minutes = models.PositiveIntegerField(
        default=0,
        help_text='Total course video duration in minutes.',
    )
    status = models.CharField(
        max_length=20,
        choices=CourseStatus.choices,
        default=CourseStatus.DRAFT,
        db_index=True,
    )
    is_published = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Denormalized flag for fast published-course queries.',
    )
    rejection_reason = models.TextField(blank=True, default='')
    published_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'nidus_courses'
        verbose_name = 'Nidus Course'
        verbose_name_plural = 'Nidus Courses'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='idx_ncourse_status_date'),
            models.Index(fields=['is_published', '-published_at'], name='idx_ncourse_pub_date'),
            models.Index(fields=['language', 'level'], name='idx_ncourse_lang_level'),
            models.Index(fields=['created_by', 'status'], name='idx_ncourse_creator_status'),
        ]

    def clean(self):
        super().clean()
        if self.created_by and self.created_by.user_type != 'instructor':
            raise ValidationError({'created_by': 'Only instructors can create courses.'})

        if self.status != self.CourseStatus.REJECTED and self.rejection_reason:
            raise ValidationError({'rejection_reason': 'Rejection reason is only valid for rejected courses.'})

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or 'course'
            candidate = base_slug
            suffix = 1
            while NidusCourse.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate

        self.is_published = self.status == self.CourseStatus.PUBLISHED
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
        if not self.is_published:
            self.published_at = None

        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.title} ({self.get_status_display()})'


class CourseLearningObjective(models.Model):
    """Outcome statements describing what students will learn."""

    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='learning_objectives',
    )
    text = models.CharField(max_length=255)
    display_order = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        db_table = 'course_learning_objectives'
        verbose_name = 'Course Learning Objective'
        verbose_name_plural = 'Course Learning Objectives'
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['course', 'text'], name='uniq_course_learning_objective_text'),
        ]
        indexes = [
            models.Index(fields=['course', 'display_order'], name='idx_clo_course_order'),
        ]

    def __str__(self):
        return self.text


class CoursePreRequisite(models.Model):
    """Pre-course requirements learners should satisfy beforehand."""

    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='prerequisites',
    )
    text = models.CharField(max_length=255)
    display_order = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        db_table = 'course_prerequisites'
        verbose_name = 'Course Pre Requisite'
        verbose_name_plural = 'Course Pre Requisites'
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['course', 'text'], name='uniq_course_prerequisite_text'),
        ]
        indexes = [
            models.Index(fields=['course', 'display_order'], name='idx_cpr_course_order'),
        ]

    def __str__(self):
        return self.text


class CourseAudience(models.Model):
    """Audience segments for whom the course is intended."""

    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='audiences',
    )
    text = models.CharField(max_length=255)
    display_order = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        db_table = 'course_audiences'
        verbose_name = 'Course Audience'
        verbose_name_plural = 'Course Audiences'
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['course', 'text'], name='uniq_course_audience_text'),
        ]
        indexes = [
            models.Index(fields=['course', 'display_order'], name='idx_caud_course_order'),
        ]

    def __str__(self):
        return self.text


class CourseSection(models.Model):
    """Logical grouping of course content (e.g., Introduction, Advanced Topics)."""

    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='sections',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    position = models.PositiveIntegerField(default=1, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'course_sections'
        verbose_name = 'Course Section'
        verbose_name_plural = 'Course Sections'
        ordering = ['course_id', 'position', 'id']
        constraints = [
            models.UniqueConstraint(fields=['course', 'position'], name='uniq_csection_course_position'),
        ]
        indexes = [
            models.Index(fields=['course', 'position'], name='idx_csection_course_position'),
        ]

    def __str__(self):
        return f'{self.course.title} - {self.title}'


class Lecture(TimestampedModel):
    """Content item inside a section. Supports video and article lectures."""

    class ContentType(models.TextChoices):
        VIDEO = 'video', 'Video'
        ARTICLE = 'article', 'Article'

    section = models.ForeignKey(
        CourseSection,
        on_delete=models.CASCADE,
        related_name='lectures',
    )
    title = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=1, db_index=True)
    content_type = models.CharField(
        max_length=20,
        choices=ContentType.choices,
        default=ContentType.VIDEO,
        db_index=True,
    )

    # Rich text/markdown/html can be stored here depending on editor strategy.
    article_content = models.TextField(blank=True, default='')
    stream_master_playlist = models.CharField(max_length=500, blank=True, default='')
    stream_renditions = models.JSONField(default=list, blank=True)
    transcoding_error = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'lectures'
        verbose_name = 'Lecture'
        verbose_name_plural = 'Lectures'
        ordering = ['section_id', 'position', 'id']
        constraints = [
            models.UniqueConstraint(fields=['section', 'position'], name='uniq_lecture_section_position'),
            models.CheckConstraint(
                check=(
                    (
                        models.Q(content_type='video')
                        & models.Q(article_content='')
                    )
                    | (
                        models.Q(content_type='article')
                        & models.Q(article_content__gt='')
                    )
                ),
                name='chk_lecture_payload_by_type',
            ),
        ]
        indexes = [
            models.Index(fields=['section', 'position'], name='idx_lecture_section_position'),
            models.Index(fields=['content_type', 'section'], name='idx_lecture_type_section'),
        ]

    def clean(self):
        super().clean()

        if self.content_type == self.ContentType.VIDEO:
            if self.article_content:
                raise ValidationError({'article_content': 'Article content must be empty for video lectures.'})

        if self.content_type == self.ContentType.ARTICLE:
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
    video_file = models.FileField(upload_to=video_asset_upload_path)
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
        if self.lecture_id and self.lecture.content_type != Lecture.ContentType.VIDEO:
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
