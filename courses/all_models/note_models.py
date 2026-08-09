from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import models

from courses.all_models.content_models import Lecture
from courses.all_models.course_models import NidusCourse, TimestampedModel


class LearnerNote(TimestampedModel):
    """
    A learner's private note, optionally anchored to a course, a lecture, and
    a playback timestamp.

    Notes are the learner's own content, so `course` and `lecture` are
    SET_NULL — a course teardown never destroys them. Consequence: an orphaned
    note keeps its title/body/tags but loses its anchor and drops out of
    `?course=` filters.
    """

    class Color(models.TextChoices):
        DEFAULT = 'default', 'Default'
        YELLOW = 'yellow', 'Yellow'
        GREEN = 'green', 'Green'
        BLUE = 'blue', 'Blue'
        PINK = 'pink', 'Pink'
        PURPLE = 'purple', 'Purple'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='learner_notes',
        help_text='Learner who owns the note.',
    )
    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='learner_notes',
        help_text='Course the note is filed under. Derived from `lecture` when omitted.',
    )
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='learner_notes',
        help_text='Lecture the note is anchored to.',
    )
    timestamp_seconds = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text='Playback position the note refers to. Requires `lecture`.',
    )
    title = models.CharField(max_length=200, blank=True, default='')
    body = models.TextField()
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text='Flat list of lowercase tag strings.',
    )
    color = models.CharField(
        max_length=20,
        choices=Color.choices,
        default=Color.DEFAULT,
    )
    is_pinned = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = 'learner_notes'
        verbose_name = 'Learner Note'
        verbose_name_plural = 'Learner Notes'
        ordering = ['-is_pinned', '-updated_at', '-id']
        constraints = [
            models.CheckConstraint(
                check=models.Q(timestamp_seconds__isnull=True) | models.Q(lecture__isnull=False),
                name='chk_note_timestamp_requires_lecture',
            ),
            models.CheckConstraint(
                check=models.Q(body__gt=''),
                name='chk_note_body_not_empty',
            ),
        ]
        indexes = [
            models.Index(fields=['user', '-is_pinned', '-updated_at'], name='idx_note_user_pin_upd'),
            models.Index(fields=['user', 'course', '-updated_at'], name='idx_note_user_course_upd'),
            models.Index(fields=['user', 'lecture'], name='idx_note_user_lecture'),
            GinIndex(fields=['tags'], name='idx_note_tags_gin'),
        ]

    def clean(self):
        super().clean()
        if self.user and self.user.user_type != 'learner':
            raise ValidationError('Only learners can create notes.')
        if self.timestamp_seconds is not None and not self.lecture_id:
            raise ValidationError({'timestamp_seconds': 'A timestamp requires a lecture.'})
        if self.lecture_id and self.course_id and self.lecture.section.course_id != self.course_id:
            raise ValidationError({'lecture': 'Lecture does not belong to the selected course.'})

    def __str__(self):
        return self.title or f'Note {self.pk}'
