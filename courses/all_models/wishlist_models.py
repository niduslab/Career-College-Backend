from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from courses.all_models.course_models import NidusCourse, TimestampedModel


class Wishlist(TimestampedModel):
    """
    A learner's saved-for-later course.

    Deliberately thin — no notes, no priority, no manual ordering. Uniqueness
    is enforced at the DB so the add endpoint can be a plain idempotent
    get_or_create without a read-then-write race.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wishlist_items',
        help_text='Learner who saved the course.',
    )
    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='wishlisted_by',
        help_text='Course saved to the wishlist.',
    )

    class Meta:
        db_table = 'course_wishlists'
        verbose_name = 'Wishlist Item'
        verbose_name_plural = 'Wishlist Items'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'course'], name='uq_wishlist_user_course'),
        ]
        indexes = [
            models.Index(fields=['user', '-created_at'], name='idx_wishlist_user_date'),
            models.Index(fields=['course'], name='idx_wishlist_course'),
        ]

    def clean(self):
        super().clean()
        if self.user and self.user.user_type != 'learner':
            raise ValidationError('Only learners can save courses to a wishlist.')
        if self.course and not self.course.is_published:
            raise ValidationError('Only published courses can be saved to a wishlist.')

    def __str__(self):
        return f'{self.user.full_name} wishlisted {self.course.title}'
