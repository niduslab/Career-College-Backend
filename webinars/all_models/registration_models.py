from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from courses.all_models.course_models import TimestampedModel

from .webinar_models import Webinar


class WebinarRegistration(TimestampedModel):
    """
    Links a learner to a published webinar.

    Free registration only (mirrors ``Enrollment`` — payment is not integrated
    yet). ``is_active`` soft-cancels; ``attended`` / ``joined_at`` are reserved
    for the live-day join flow (a later phase).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='webinar_registrations',
        help_text='Learner registered for the webinar.',
    )
    webinar = models.ForeignKey(
        Webinar,
        on_delete=models.CASCADE,
        related_name='registrations',
        help_text='Webinar the learner registered for.',
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text='False when the learner cancels (soft revoke).',
    )
    attended = models.BooleanField(
        default=False,
        help_text='Set when the learner joins the live session (later phase).',
    )
    joined_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'webinar_registrations'
        verbose_name = 'Webinar Registration'
        verbose_name_plural = 'Webinar Registrations'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'webinar'],
                name='uniq_registration_user_webinar',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active', '-created_at'], name='idx_wreg_user_active'),
            models.Index(fields=['webinar', 'is_active'], name='idx_wreg_webinar_active'),
        ]

    def clean(self):
        super().clean()
        if self.user and self.user.user_type != 'learner':
            raise ValidationError('Only learners can register for webinars.')
        if self.webinar and not self.webinar.is_published:
            raise ValidationError('Registration is only allowed for published webinars.')

    def __str__(self):
        return f'{self.user.full_name} registered for {self.webinar.title}'
