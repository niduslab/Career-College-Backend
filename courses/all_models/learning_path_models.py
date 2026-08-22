from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from courses.all_models.course_models import AuthoredModel, NidusCourse, TimestampedModel


class LearningPath(AuthoredModel):
    """
    A curated, ordered sequence of existing courses toward a named career
    goal (e.g. "AI/ML Engineer"). Milestones point at courses that already
    exist — a path arranges content, it never duplicates it. See
    docs/architecture/28-learning-paths.md.
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED = 'archived', 'Archived'

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, db_index=True)
    description = models.TextField(blank=True, default='')
    career_goal = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text='Hero heading shown on the path page. Falls back to title when blank.',
    )
    skill_tags = models.JSONField(
        default=list,
        blank=True,
        help_text='Flat list of display-only skill strings, e.g. ["Python", "MLOps"].',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    class Meta:
        db_table = 'learning_paths'
        verbose_name = 'Learning Path'
        verbose_name_plural = 'Learning Paths'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='idx_lpath_status_created'),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or 'path'
            candidate = base_slug
            suffix = 1
            while LearningPath.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class LearningPathMilestone(models.Model):
    """
    One step of a LearningPath, pointing at an existing published course.

    `course` is PROTECT — deleting a course that's a live milestone must
    fail loudly so an author explicitly removes the milestone first, rather
    than a published path silently losing a step.
    """

    path = models.ForeignKey(
        LearningPath,
        on_delete=models.CASCADE,
        related_name='milestones',
    )
    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.PROTECT,
        related_name='learning_path_milestones',
    )
    position = models.PositiveIntegerField()
    title = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text='Stage label override, e.g. "Programming Foundations". Falls back to the course title when blank.',
    )

    class Meta:
        db_table = 'learning_path_milestones'
        verbose_name = 'Learning Path Milestone'
        verbose_name_plural = 'Learning Path Milestones'
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(fields=['path', 'position'], name='uq_lpath_milestone_position'),
            models.UniqueConstraint(fields=['path', 'course'], name='uq_lpath_milestone_course'),
        ]
        indexes = [
            models.Index(fields=['path', 'position'], name='idx_lpath_milestone_order'),
        ]

    def __str__(self):
        return f'{self.path.title} — #{self.position} {self.title or self.course.title}'


class LearningPathEnrollment(TimestampedModel):
    """
    A learner's opt-in to a LearningPath. Deliberately thin — no progress
    field. Progress is always derived from the learner's real course
    Enrollment rows (see docs/architecture/28-learning-paths.md §4), so
    there is nothing here that can drift out of sync with reality.

    Leaving a path never touches the learner's course enrollments.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='learning_path_enrollments',
    )
    path = models.ForeignKey(
        LearningPath,
        on_delete=models.CASCADE,
        related_name='enrollments',
    )

    class Meta:
        db_table = 'learning_path_enrollments'
        verbose_name = 'Learning Path Enrollment'
        verbose_name_plural = 'Learning Path Enrollments'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'path'], name='uq_lpath_enrollment_user_path'),
        ]
        indexes = [
            models.Index(fields=['user', '-created_at'], name='idx_lpath_enroll_user_date'),
        ]

    def clean(self):
        super().clean()
        if self.user and self.user.user_type != 'learner':
            raise ValidationError('Only learners can enroll in a learning path.')
        if self.path_id and self.path.status != LearningPath.Status.PUBLISHED:
            raise ValidationError('Only published learning paths can be enrolled in.')

    def __str__(self):
        return f'{self.user.full_name} → {self.path.title}'
