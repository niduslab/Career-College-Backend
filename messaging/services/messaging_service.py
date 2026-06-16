"""
Messaging service — all business logic for conversations and messages.

Send-gate rules (applied at message-send time, not conversation-open time):
  Learner  : Enrollment(user=learner, course=course, is_active=True) must exist.
  Instructor: instructor must still be in course.instructors.all().

Access policy (numeric IDs → 404 on no-access; slug-based → 403):
  get_conversation_for_participant raises Conversation.DoesNotExist when the
  caller is not a participant, yielding a 404 in the view layer — consistent
  with the project-wide rule for numeric-ID resources.
"""

import logging
from typing import Optional

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from messaging.models import Conversation, Message

logger = logging.getLogger(__name__)


class MessagingError(Exception):
    """Raised by messaging service functions for domain-rule violations."""

    def __init__(self, message: str, http_status: int = 422):
        self.message = message
        self.http_status = http_status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Internal permission guards
# ---------------------------------------------------------------------------

def _assert_active_enrollment(learner, course) -> None:
    """Raises MessagingError(403) if no active enrollment exists."""
    from courses.all_models.enrollment_models import Enrollment
    if not Enrollment.objects.filter(user=learner, course=course, is_active=True).exists():
        raise MessagingError(
            'You must be actively enrolled in this course to message an instructor.',
            http_status=403,
        )


def _assert_instructor_on_course(instructor, course) -> None:
    """Raises MessagingError(403) if instructor is not in course.instructors."""
    if not course.instructors.filter(pk=instructor.pk).exists():
        raise MessagingError(
            'This instructor is not a member of this course.',
            http_status=403,
        )


def _assert_send_permission(sender, conversation: Conversation) -> None:
    """
    Checks the send-gate for an existing conversation.

    Learner side : active enrollment required.
    Instructor side: must still be on the course.
    """
    if sender.pk == conversation.learner_id:
        from courses.all_models.enrollment_models import Enrollment
        if not Enrollment.objects.filter(
            user=sender,
            course_id=conversation.course_id,
            is_active=True,
        ).exists():
            raise MessagingError(
                'You must be actively enrolled to send messages in this course.',
                http_status=403,
            )
    elif sender.pk == conversation.instructor_id:
        # Reload course to avoid stale M2M cache on cached conversation objects.
        from courses.all_models.course_models import NidusCourse
        if not NidusCourse.objects.filter(
            pk=conversation.course_id,
            instructors=sender,
        ).exists():
            raise MessagingError(
                'You are no longer an instructor for this course.',
                http_status=403,
            )
    else:
        # Should never happen if get_conversation_for_participant is called first,
        # but guard defensively.
        raise MessagingError(
            'You are not a participant in this conversation.',
            http_status=403,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_or_create_conversation(
    learner,
    instructor,
    course,
    opener_body: str,
) -> tuple[Conversation, bool]:
    """
    Get existing or atomically create a new Conversation + first Message.

    Returns (conversation, created: bool).
    Raises MessagingError on permission violations.

    The opener_body is only persisted when created=True. When the conversation
    already exists the caller receives the existing row without a new message,
    matching the recommended UX of "take the user to the existing thread".
    """
    _assert_active_enrollment(learner, course)
    _assert_instructor_on_course(instructor, course)

    with transaction.atomic():
        try:
            conversation, created = Conversation.objects.get_or_create(
                learner=learner,
                instructor=instructor,
                course=course,
            )
        except IntegrityError:
            # Another concurrent request created the row between our SELECT and INSERT.
            conversation = Conversation.objects.get(
                learner=learner,
                instructor=instructor,
                course=course,
            )
            created = False

        if created:
            message = Message.objects.create(
                conversation=conversation,
                sender=learner,
                body=opener_body,
            )
            _schedule_new_message_dispatch(
                conversation=conversation,
                message=message,
                sender=learner,
                recipient_id=instructor.pk,
                course_slug=course.slug,
                course_title=course.title,
                sender_name=learner.full_name,
            )

    return conversation, created


def list_conversations(user) -> QuerySet:
    """
    Return all conversations for a user (learner or instructor), most-recently
    active first. Callers can annotate or paginate the returned queryset.
    """
    return (
        Conversation.objects
        .filter(Q(learner=user) | Q(instructor=user))
        .select_related('learner', 'instructor', 'course')
        .order_by('-updated_at')
    )


def get_conversation_for_participant(user, conversation_id: int) -> Conversation:
    """
    Fetch a conversation by numeric ID. Raises Conversation.DoesNotExist when
    the row doesn't exist OR the caller is not a participant — both surface as
    404 per the project's numeric-ID access-denied policy.
    """
    try:
        return Conversation.objects.select_related(
            'learner', 'instructor', 'course'
        ).get(
            Q(pk=conversation_id) & (Q(learner=user) | Q(instructor=user))
        )
    except Conversation.DoesNotExist:
        raise


def get_messages(conversation: Conversation) -> QuerySet:
    """Return visible (non-deleted) messages for a conversation, oldest first."""
    return (
        Message.objects
        .filter(conversation=conversation, is_deleted=False)
        .select_related('sender')
        .order_by('created_at')
    )


def send_message(user, conversation_id: int, body: str) -> Message:
    """
    Create a new Message in an existing conversation.

    Raises:
        Conversation.DoesNotExist  — caller is not a participant (→ 404).
        MessagingError(403)        — send-gate violated (unenrolled / removed instructor).
    """
    conversation = get_conversation_for_participant(user, conversation_id)
    _assert_send_permission(user, conversation)

    recipient_id = (
        conversation.instructor_id
        if user.pk == conversation.learner_id
        else conversation.learner_id
    )

    with transaction.atomic():
        message = Message.objects.create(
            conversation=conversation,
            sender=user,
            body=body,
        )
        # Touch updated_at for list ordering without triggering auto_now on Conversation
        # (auto_now fires on full .save(); a targeted UPDATE avoids it and is cheaper).
        Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())

        _schedule_new_message_dispatch(
            conversation=conversation,
            message=message,
            sender=user,
            recipient_id=recipient_id,
            course_slug=conversation.course.slug,
            course_title=conversation.course.title,
            sender_name=user.full_name,
        )

    return message


def mark_read(user, conversation_id: int) -> None:
    """
    Record that the caller has read all messages in a conversation up to now.
    Updates the appropriate *_last_read_at timestamp in one query.

    Raises Conversation.DoesNotExist if caller is not a participant.
    """
    conversation = get_conversation_for_participant(user, conversation_id)
    now = timezone.now()
    if user.pk == conversation.learner_id:
        Conversation.objects.filter(pk=conversation.pk).update(learner_last_read_at=now)
    else:
        Conversation.objects.filter(pk=conversation.pk).update(instructor_last_read_at=now)


def get_unread_counts(user) -> list[dict]:
    """
    Return a list of {conversation_id, unread_count} dicts for conversations
    where the caller has unread messages. Used by the WS on-connect handler.

    This does N+1 count queries (one per conversation). Acceptable because
    users typically have O(10–50) conversations and this only runs at connect time.
    """
    conversations = list(
        Conversation.objects.filter(
            Q(learner=user) | Q(instructor=user)
        ).only('pk', 'learner_id', 'instructor_id', 'learner_last_read_at', 'instructor_last_read_at')
    )
    result = []
    for conv in conversations:
        last_read = (
            conv.learner_last_read_at
            if user.pk == conv.learner_id
            else conv.instructor_last_read_at
        )
        qs = Message.objects.filter(conversation=conv, is_deleted=False)
        count = qs.filter(created_at__gt=last_read).count() if last_read else qs.count()
        if count > 0:
            result.append({'conversation_id': conv.pk, 'unread_count': count})
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _schedule_new_message_dispatch(
    *,
    conversation: Conversation,
    message: Message,
    sender,
    recipient_id: int,
    course_slug: str,
    course_title: str,
    sender_name: str,
) -> None:
    """Register on_commit callbacks for WS push + notification. Never raises."""
    message_snapshot = _serialize_message(message)
    conv_id = conversation.pk
    sender_id = sender.pk

    transaction.on_commit(
        lambda: _push_ws_and_notify(
            conversation_id=conv_id,
            message_snapshot=message_snapshot,
            sender_id=sender_id,
            recipient_id=recipient_id,
            course_slug=course_slug,
            course_title=course_title,
            sender_name=sender_name,
        )
    )


def _push_ws_and_notify(
    *,
    conversation_id: int,
    message_snapshot: dict,
    sender_id: int,
    recipient_id: int,
    course_slug: str,
    course_title: str,
    sender_name: str,
) -> None:
    """Push new_message to the recipient and send a notification to them."""
    channel_layer = get_channel_layer()
    if channel_layer is not None:
        # Push only to the recipient's channel group. The sender already has the
        # message: via message_sent (WS path) or the 201 response body (REST path).
        # Pushing to the sender group too would cause duplicate delivery on the WS
        # path, requiring client-side dedup by message.id.
        try:
            async_to_sync(channel_layer.group_send)(
                f'messaging_user_{recipient_id}',
                {
                    'type': 'messaging.new_message',
                    'conversation_id': conversation_id,
                    'message': message_snapshot,
                },
            )
        except Exception:
            logger.warning(
                'WS push failed for messaging: conversation=%s recipient=%s',
                conversation_id, recipient_id,
            )

    try:
        from authentication.models import User
        from notifications.models import NotificationEventType
        from notifications.services.dispatcher import dispatch

        recipient = User.objects.get(pk=recipient_id)
        dispatch(
            NotificationEventType.MESSAGE_RECEIVED,
            [recipient],
            context={
                'conversation_id': conversation_id,
                'course_slug': course_slug,
                'course_title': course_title,
                'sender_name': sender_name,
                'body_preview': message_snapshot['body'][:120],
            },
        )
    except Exception:
        logger.warning(
            'MESSAGE_RECEIVED notification dispatch failed: conversation=%s',
            conversation_id,
        )


def _serialize_message(message: Message) -> dict:
    """Minimal dict representation used in WS payloads and on_commit closures."""
    return {
        'id': message.pk,
        'conversation_id': message.conversation_id,
        'sender_id': message.sender_id,
        'body': message.body,
        'is_deleted': message.is_deleted,
        'created_at': message.created_at.isoformat(),
    }
