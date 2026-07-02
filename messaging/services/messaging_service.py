"""
Messaging service — all business logic for conversations and messages.

Conversations are 2-party threads. `conversation_type` selects the party roles
and the send-gate; parties live in `ConversationParticipant`. All send-gates are
applied at message-send time (not conversation-open time) and are enforced only
here — never duplicated in a view or the WebSocket consumer.

Send-gate by type:
  learner_instructor : learner needs an active enrollment; instructor must still
                       be on the course.
  co_instructor      : each instructor must still be on the course roster.
  institution_expert : the expert must still be an active affiliate; the
                       institution party may always send.

Access policy (numeric IDs → 404 on no-access): get_conversation_for_participant
raises Conversation.DoesNotExist when the caller is not a participant, yielding a
404 in the view layer.
"""

import datetime
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, QuerySet, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from messaging.models import Conversation, ConversationParticipant, Message

logger = logging.getLogger(__name__)

_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
_CType = Conversation.ConversationType


class MessagingError(Exception):
    """Raised by messaging service functions for domain-rule violations."""

    def __init__(self, message: str, http_status: int = 422):
        self.message = message
        self.http_status = http_status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Low-level guards (raise MessagingError)
# ---------------------------------------------------------------------------

def _assert_active_enrollment(learner, course_id) -> None:
    from courses.all_models.enrollment_models import Enrollment
    if not Enrollment.objects.filter(user=learner, course_id=course_id, is_active=True).exists():
        raise MessagingError(
            'You must be actively enrolled in this course to message an instructor.',
            http_status=403,
        )


def _assert_instructor_on_course(instructor, course_id) -> None:
    from courses.all_models.course_models import NidusCourse
    if not NidusCourse.objects.filter(pk=course_id, instructors=instructor).exists():
        raise MessagingError(
            'You are not an instructor for this course.',
            http_status=403,
        )


def _assert_active_affiliation(expert_user, institution_user) -> None:
    from authentication.models import InstructorProfile
    ok = InstructorProfile.objects.filter(
        user=expert_user,
        affiliated_institution__user=institution_user,
        affiliation_status='active',
    ).exists()
    if not ok:
        raise MessagingError(
            'You are no longer an active member of this institution.',
            http_status=403,
        )


def _pair_key(user_id_a: int, user_id_b: int) -> str:
    lo, hi = sorted((user_id_a, user_id_b))
    return f'{lo}-{hi}'


def _other_participant(conversation: Conversation, user):
    """The other party's ConversationParticipant (participants are prefetched)."""
    for p in conversation.participants.all():
        if p.user_id != user.pk:
            return p
    return None


# ---------------------------------------------------------------------------
# Send-gate dispatch
# ---------------------------------------------------------------------------

def _assert_send_permission(sender, conversation: Conversation) -> None:
    ctype = conversation.conversation_type
    if ctype == _CType.LEARNER_INSTRUCTOR:
        if sender.user_type == 'learner':
            _assert_active_enrollment(sender, conversation.course_id)
        else:
            _assert_instructor_on_course(sender, conversation.course_id)
    elif ctype == _CType.CO_INSTRUCTOR:
        _assert_instructor_on_course(sender, conversation.course_id)
    elif ctype == _CType.INSTITUTION_EXPERT:
        if sender.user_type != 'partner_institution':
            # Institution party is the prefetched counterpart — no extra query.
            institution = _other_participant(conversation, sender)
            _assert_active_affiliation(sender, institution.user)
    else:
        raise MessagingError('Unknown conversation type.', http_status=422)


def _validate_new_conversation(conversation_type, initiator, target, course) -> None:
    """Creation-time gate: the pair is valid and the initiator may open it.

    Role checks come before the self-pair check so a wrong-role initiator gets a
    403 (not a 400) even when they pass their own id as the target.
    """
    if conversation_type == _CType.LEARNER_INSTRUCTOR:
        if course is None:
            raise MessagingError('A course is required for this conversation.', http_status=400)
        if initiator.user_type != 'learner' or target.user_type != 'instructor':
            raise MessagingError(
                'Only a learner can start a conversation with an instructor.', http_status=403,
            )
        _assert_no_self(initiator, target)
        _assert_active_enrollment(initiator, course.pk)
        _assert_instructor_on_course(target, course.pk)

    elif conversation_type == _CType.CO_INSTRUCTOR:
        if course is None:
            raise MessagingError('A course is required for this conversation.', http_status=400)
        if initiator.user_type != 'instructor' or target.user_type != 'instructor':
            raise MessagingError('Both parties must be instructors.', http_status=403)
        _assert_no_self(initiator, target)
        _assert_instructor_on_course(initiator, course.pk)
        _assert_instructor_on_course(target, course.pk)

    elif conversation_type == _CType.INSTITUTION_EXPERT:
        institution, expert = _resolve_institution_expert(initiator, target)
        _assert_no_self(initiator, target)
        _assert_active_affiliation(expert, institution)

    else:
        raise MessagingError('Unknown conversation type.', http_status=400)


def _assert_no_self(initiator, target) -> None:
    if initiator.pk == target.pk:
        raise MessagingError('Cannot start a conversation with yourself.', http_status=400)


def _resolve_institution_expert(initiator, target):
    """Return (institution_user, expert_user) from an unordered pair; else raise."""
    parties = {initiator.user_type: initiator, target.user_type: target}
    institution = parties.get('partner_institution')
    expert = parties.get('instructor')
    if institution is None or expert is None:
        raise MessagingError(
            'An institution conversation is between a partner institution and an expert.',
            http_status=403,
        )
    return institution, expert


# ---------------------------------------------------------------------------
# Public API — creation
# ---------------------------------------------------------------------------

def start_conversation(*, conversation_type, initiator, target, course=None, opener_body):
    """
    Get an existing thread or atomically create it + its first Message.

    Returns (conversation, created). Raises MessagingError on gate violations.
    The opener_body is persisted only when created=True; an existing thread is
    returned untouched so the client navigates to it.
    """
    _validate_new_conversation(conversation_type, initiator, target, course)
    key = _pair_key(initiator.pk, target.pk)

    with transaction.atomic():
        conversation, created = _get_or_create(conversation_type, key, course, initiator, target)
        if created:
            message = Message.objects.create(
                conversation=conversation, sender=initiator, body=opener_body,
            )
            _schedule_new_message_dispatch(
                conversation=conversation, message=message,
                sender=initiator, recipient_id=target.pk,
            )

    return conversation, created


def _get_or_create(conversation_type, key, course, initiator, target):
    existing = Conversation.objects.filter(
        conversation_type=conversation_type, participant_key=key, course=course,
    ).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            conversation = Conversation.objects.create(
                conversation_type=conversation_type, participant_key=key, course=course,
            )
            ConversationParticipant.objects.create(conversation=conversation, user=initiator)
            ConversationParticipant.objects.create(conversation=conversation, user=target)
        return conversation, True
    except IntegrityError:
        # Lost a race; return the row the other request created.
        return Conversation.objects.get(
            conversation_type=conversation_type, participant_key=key, course=course,
        ), False


def get_or_create_conversation(learner, instructor, course, opener_body):
    """Back-compat shim for the learner↔instructor path (learner-initiated)."""
    return start_conversation(
        conversation_type=_CType.LEARNER_INSTRUCTOR,
        initiator=learner, target=instructor, course=course, opener_body=opener_body,
    )


# ---------------------------------------------------------------------------
# Public API — reads & sends
# ---------------------------------------------------------------------------

def list_conversations(user) -> QuerySet:
    """All conversations the user participates in, most-recently active first."""
    return (
        Conversation.objects
        .filter(participants__user=user)
        .select_related('course')
        .prefetch_related('participants__user')
        .order_by('-updated_at')
    )


def get_conversation_for_participant(user, conversation_id: int) -> Conversation:
    """
    Fetch a conversation by numeric ID. Raises Conversation.DoesNotExist when the
    row doesn't exist OR the caller is not a participant — both → 404.
    """
    return (
        Conversation.objects
        .select_related('course')
        .prefetch_related('participants__user')
        .get(pk=conversation_id, participants__user=user)
    )


def get_messages(conversation: Conversation) -> QuerySet:
    """Visible (non-deleted) messages for a conversation, oldest first."""
    return (
        Message.objects
        .filter(conversation=conversation, is_deleted=False)
        .select_related('sender')
        .order_by('created_at')
    )


def send_message(user, conversation_id: int, body: str) -> Message:
    """
    Create a new Message in an existing conversation.

    Raises Conversation.DoesNotExist (caller not a participant → 404) or
    MessagingError(403) (send-gate violated).
    """
    conversation = get_conversation_for_participant(user, conversation_id)
    _assert_send_permission(user, conversation)
    other = _other_participant(conversation, user)
    recipient_id = other.user_id if other else None

    with transaction.atomic():
        message = Message.objects.create(conversation=conversation, sender=user, body=body)
        # Bump updated_at for list ordering without triggering auto_now.
        Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
        _schedule_new_message_dispatch(
            conversation=conversation, message=message,
            sender=user, recipient_id=recipient_id,
        )

    return message


def mark_read(user, conversation_id: int) -> None:
    """Stamp the caller's read cursor to now. Raises DoesNotExist if not a participant."""
    conversation = get_conversation_for_participant(user, conversation_id)
    ConversationParticipant.objects.filter(
        conversation=conversation, user=user,
    ).update(last_read_at=timezone.now())


def get_unread_conversation_count(user) -> int:
    """Number of the caller's conversations with at least one unread message."""
    unread_msgs = Message.objects.filter(
        conversation=OuterRef('conversation_id'),
        is_deleted=False,
        created_at__gt=Coalesce(OuterRef('last_read_at'), Value(_EPOCH)),
    )
    return (
        ConversationParticipant.objects
        .filter(user=user)
        .filter(Exists(unread_msgs))
        .count()
    )


def get_unread_counts(user) -> list[dict]:
    """
    Per-conversation unread message counts for the caller (used by the WS
    on-connect handler). One count query per participant row — acceptable at
    connect time for O(10–50) conversations.
    """
    parts = ConversationParticipant.objects.filter(user=user).only(
        'conversation_id', 'last_read_at',
    )
    result = []
    for p in parts:
        qs = Message.objects.filter(conversation_id=p.conversation_id, is_deleted=False)
        count = qs.filter(created_at__gt=p.last_read_at).count() if p.last_read_at else qs.count()
        if count > 0:
            result.append({'conversation_id': p.conversation_id, 'unread_count': count})
    return result


# ---------------------------------------------------------------------------
# Internal helpers — WS push + notification
# ---------------------------------------------------------------------------

def _schedule_new_message_dispatch(*, conversation, message, sender, recipient_id) -> None:
    """Register on_commit callbacks for WS push + notification. Never raises."""
    if recipient_id is None:
        return
    message_snapshot = _serialize_message(message)
    course = conversation.course
    conv_id = conversation.pk
    sender_id = sender.pk
    sender_name = sender.full_name
    course_slug = course.slug if course else None
    course_title = course.title if course else None

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


def _push_ws_and_notify(*, conversation_id, message_snapshot, sender_id, recipient_id,
                        course_slug, course_title, sender_name) -> None:
    """Push new_message to the recipient and dispatch a notification. Never raises."""
    channel_layer = get_channel_layer()
    if channel_layer is not None:
        # Only the recipient — the sender already has the message via the ack / 201.
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
                'body_preview': message_snapshot['body'],
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
