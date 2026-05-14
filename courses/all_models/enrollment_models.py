from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models

from courses.all_models.course_models import NidusCourse, TimestampedModel


class Enrollment(TimestampedModel):
    """
    Links a learner to a published course.

    This is the gateway record: without an active enrollment the learner
    can see course metadata (catalog) but cannot access lectures, quizzes,
    or assignments.
    """

    class EnrollmentType(models.TextChoices):
        FREE = 'free', 'Free'
        PAID = 'paid', 'Paid'
        ADMIN_GRANTED = 'admin_granted', 'Admin Granted'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='enrollments',
        help_text='Learner enrolled in the course.',
    )
    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='enrollments',
        help_text='Course the learner is enrolled in.',
    )
    enrollment_type = models.CharField(
        max_length=15,
        choices=EnrollmentType.choices,
        default=EnrollmentType.FREE,
        db_index=True,
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text='False when the learner unenrolls (soft revoke).',
    )
    progress_percent = models.PositiveIntegerField(
        default=0,
        validators=[MaxValueValidator(100)],
        help_text='Denormalized 0-100 completion percentage.',
    )
    completed_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text='Timestamp when progress_percent first reached 100.',
    )
    last_accessed_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text='Last time the learner accessed course content.',
    )

    class Meta:
        db_table = 'enrollments'
        verbose_name = 'Enrollment'
        verbose_name_plural = 'Enrollments'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'course'],
                name='uniq_enrollment_user_course',
            ),
            models.CheckConstraint(
                check=models.Q(progress_percent__lte=100),
                name='chk_enrollment_progress_percent_lte_100',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active', '-last_accessed_at'], name='idx_enroll_user_active_last'),
            models.Index(fields=['course', 'is_active'], name='idx_enroll_course_active'),
            models.Index(fields=['enrollment_type'], name='idx_enroll_type'),
        ]

    def clean(self):
        super().clean()
        if self.user and self.user.user_type != 'learner':
            raise ValidationError('Only learners can enroll in courses.')
        if self.course and not self.course.is_published:
            raise ValidationError('Enrollment is only allowed for published courses.')

    def __str__(self):
        return f'{self.user.full_name} enrolled in {self.course.title}'
