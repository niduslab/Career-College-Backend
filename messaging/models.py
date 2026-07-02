import logging

from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


class Conversation(models.Model):
    """
    A 2-party direct-message thread. `conversation_type` selects the party roles
    and the send-gate; the two parties live in `ConversationParticipant` so the
    model is role-neutral and one set of threading/unread logic serves every type.

    Types:
      learner_instructor — learner ↔ course instructor (learner-initiated).
      co_instructor      — two instructors on the same course roster.
      institution_expert — a partner institution ↔ one of its affiliated experts.

    Send-gates (enforced in the service, not here) are chosen by type. Unread
    state is a per-user cursor on each participant row, not per-message flags.
    """

    class ConversationType(models.TextChoices):
        LEARNER_INSTRUCTOR = 'learner_instructor', 'Learner and Instructor'
        CO_INSTRUCTOR = 'co_instructor', 'Co-instructors'
        INSTITUTION_EXPERT = 'institution_expert', 'Institution and Expert'

    conversation_type = models.CharField(
        max_length=32,
        choices=ConversationType.choices,
        default=ConversationType.LEARNER_INSTRUCTOR,
        db_index=True,
    )
    course = models.ForeignKey(
        'courses.NidusCourse',
        on_delete=models.CASCADE,
        related_name='conversations',
        null=True,
        blank=True,
        db_index=True,
        help_text='Course context. Required for course-scoped types; null for institution_expert.',
    )
    participant_key = models.CharField(
        max_length=64,
        default='',
        db_index=True,
        help_text='Deterministic "<minid>-<maxid>" of the two participant user ids; enforces pair uniqueness.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Intentionally NOT auto_now: the service bumps this with a targeted .update()
    # on new messages, which auto_now would silently ignore.
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'conversations'
        verbose_name = 'Conversation'
        verbose_name_plural = 'Conversations'
        ordering = ['-updated_at']
        constraints = [
            # A pair has one thread per (type, course). Two partial constraints
            # because a NULL course does not participate in a normal unique index.
            # Intended consequence: the same pair MAY hold both a course-bound
            # institution_expert thread and a course-less one — they are distinct
            # contexts (a specific course vs a general institution↔expert channel).
            models.UniqueConstraint(
                fields=['conversation_type', 'course', 'participant_key'],
                condition=models.Q(course__isnull=False),
                name='uniq_conv_type_course_pair',
            ),
            models.UniqueConstraint(
                fields=['conversation_type', 'participant_key'],
                condition=models.Q(course__isnull=True),
                name='uniq_conv_type_pair_no_course',
            ),
        ]
        indexes = [
            models.Index(fields=['conversation_type', '-updated_at'], name='idx_conv_type_updated'),
        ]

    def __str__(self) -> str:
        return f'Conv#{self.pk} [{self.conversation_type}] key={self.participant_key} course={self.course_id}'


class ConversationParticipant(models.Model):
    """One party in a Conversation, carrying that user's per-thread read cursor."""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='participants',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversation_participations',
    )
    last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last time this user read the thread; used for unread counts.',
    )

    class Meta:
        db_table = 'conversation_participants'
        verbose_name = 'Conversation Participant'
        verbose_name_plural = 'Conversation Participants'
        constraints = [
            models.UniqueConstraint(fields=['conversation', 'user'], name='uniq_conv_participant'),
        ]
        indexes = [
            models.Index(fields=['user', 'conversation'], name='idx_convpart_user_conv'),
        ]

    def __str__(self) -> str:
        return f'Participant user={self.user_id} in Conv#{self.conversation_id}'


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
