import logging

from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


class Conversation(models.Model):
    """
    One direct-message thread between a learner and an instructor, scoped to a
    specific course. Only the learner can initiate; either party can reply.

    Access rules (enforced in service layer):
    - Learner must have an active Enrollment (is_active=True) to *send*.
    - Instructor must still be in course.instructors.all() to *send*.
    - Either party can read historical messages regardless of current enrollment
      or instructor status.

    Unread state is tracked via learner_last_read_at / instructor_last_read_at
    rather than per-message flags, so marking a thread read is a single UPDATE
    instead of N UPDATEs.
    """

    learner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='learner_conversations',
        db_index=True,
    )
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='instructor_conversations',
        db_index=True,
    )
    course = models.ForeignKey(
        'courses.NidusCourse',
        on_delete=models.CASCADE,
        related_name='conversations',
        db_index=True,
    )
    learner_last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last time the learner read this thread; used for unread count.',
    )
    instructor_last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last time the instructor read this thread; used for unread count.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Intentionally NOT auto_now: the service uses a targeted .update() to bump
    # this on new messages, which would be silently ignored on an auto_now field.
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'conversations'
        verbose_name = 'Conversation'
        verbose_name_plural = 'Conversations'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['learner', 'instructor', 'course'],
                name='uniq_conversation_learner_instructor_course',
            ),
            models.CheckConstraint(
                check=~models.Q(learner=models.F('instructor')),
                name='chk_conversation_learner_ne_instructor',
            ),
        ]
        indexes = [
            models.Index(fields=['learner', '-updated_at'], name='idx_conv_learner_updated'),
            models.Index(fields=['instructor', '-updated_at'], name='idx_conv_instructor_updated'),
        ]

    def __str__(self) -> str:
        return f'Conv#{self.pk}: learner={self.learner_id} ↔ instructor={self.instructor_id} (course={self.course_id})'


class Message(models.Model):
    """
    A single message within a Conversation. Soft-deleted rows (is_deleted=True)
    are excluded from client payloads but kept for audit/moderation purposes.
    """

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages',
    )
    body = models.TextField()
    is_deleted = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Soft-delete flag. Deleted messages are hidden from clients but not purged.',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'messages'
        verbose_name = 'Message'
        verbose_name_plural = 'Messages'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at'], name='idx_msg_conv_created'),
        ]

    def __str__(self) -> str:
        return f'Msg#{self.pk} by sender={self.sender_id} in Conv#{self.conversation_id}'
