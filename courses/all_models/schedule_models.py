import logging

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from courses.all_models.course_models import AuthoredModel, NidusCourse

logger = logging.getLogger(__name__)


class CourseSchedule(AuthoredModel):
    """
    A scheduled cohort run of a course (see docs/future_implementations/SCHEDULED_COURSES.md).

    The course stays the reusable curriculum template; the schedule holds the
    enrollment window, start/end dates, and seat cap. One course can have many
    schedules over time (repeat cohorts).
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SCHEDULED = 'scheduled', 'Scheduled'
        ONGOING = 'ongoing', 'Ongoing'
        COMPLETED = 'completed', 'Completed'
        ARCHIVED = 'archived', 'Archived'

    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='schedules',
    )
    cohort_label = models.CharField(
        max_length=100,
        blank=True,
        help_text='Human-readable cohort name, e.g. "Fall 2026 Batch".',
    )
    timezone = models.CharField(
        max_length=64,
        default='UTC',
        help_text='IANA timezone the dates are presented in.',
    )
    enrollment_opens_at = models.DateTimeField()
    enrollment_closes_at = models.DateTimeField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(
        blank=True,
        null=True,
        help_text='Null = open-ended; the schedule stays ongoing until set.',
    )
    max_seats = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text='Null = unlimited seats.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    class Meta:
        db_table = 'course_schedules'
        verbose_name = 'Course Schedule'
        verbose_name_plural = 'Course Schedules'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['course', 'status'], name='idx_cschedule_course_status'),
        ]

    def __str__(self):
        label = self.cohort_label or f'Schedule {self.pk}'
        return f'{self.course.title} - {label}'

    # Editable statuses — dates/seats may change until the cohort starts.
    EDITABLE_STATUSES = frozenset(('draft', 'scheduled'))

    def is_editable(self):
        return self.status in self.EDITABLE_STATUSES

    # Status transition state machine
    VALID_TRANSITIONS = {
        'draft': ('scheduled',),
        'scheduled': ('ongoing', 'draft'),
        'ongoing': ('completed',),
        'completed': ('archived',),
        'archived': ('draft',),
    }

    def transition_to(self, new_status, actor=None):
        """
        Move the schedule to *new_status* with guard-rail checks.
        Raises ``ValidationError`` on illegal transitions or incomplete data.
        """
        allowed = self.VALID_TRANSITIONS.get(self.status, ())
        if new_status not in allowed:
            raise ValidationError(
                f'Cannot transition from "{self.status}" to "{new_status}". '
                f'Allowed: {", ".join(allowed) if allowed else "none (terminal state)"}.'
            )

        if new_status == self.Status.SCHEDULED and self.status == self.Status.DRAFT:
            self._validate_activation()

        self.status = new_status
        self.save()

    def date_logic_errors(self):
        """
        Structural date-ordering problems only — no "is it still in the future"
        check. Shared by _validate_activation() (which additionally checks
        against "now") and NidusCourse._validate_course_completeness() (which
        intentionally skips the "future" check, since submission and admin
        approval can be far apart in time).
        """
        errors = {}
        if self.enrollment_opens_at >= self.enrollment_closes_at:
            errors['enrollment_opens_at'] = 'Enrollment must open before it closes.'
        if self.enrollment_closes_at > self.start_date:
            errors['enrollment_closes_at'] = 'Enrollment must close on or before the start date.'
        if self.end_date is not None and self.end_date <= self.start_date:
            errors['end_date'] = 'End date must be after the start date.'
        return errors

    def _validate_activation(self):
        """Ensure the schedule is ready before activation. Collects all problems."""
        errors = {}

        if self.course.status != 'published':
            errors['course'] = 'The course must be published before a schedule can be activated.'

        errors.update(self.date_logic_errors())

        now = timezone.now()
        if self.enrollment_closes_at <= now:
            errors.setdefault('enrollment_closes_at', 'Enrollment close time must be in the future.')
        if self.start_date <= now:
            errors['start_date'] = 'Start date must be in the future.'

        if errors:
            raise ValidationError(errors)
