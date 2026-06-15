import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import IntegrityError, transaction

from notifications.models import Notification
from notifications.serializers import NotificationSerializer
from .builders import build_notification_payload
from .preference_service import get_email_preference

logger = logging.getLogger(__name__)


def dispatch(
    event_type: str,
    recipients,
    context: dict,
    skip_email: bool = False,
) -> None:
    """Persist, WS-push, and optionally email a notification for each recipient. Never raises."""
    if not recipients:
        return

    channel_layer = get_channel_layer()

    for recipient in recipients:
        try:
            _dispatch_one(event_type, recipient, context, skip_email, channel_layer)
        except Exception:
            logger.exception(
                'dispatch failed: event_type=%s recipient=%s', event_type, getattr(recipient, 'id', recipient)
            )


def _dispatch_one(event_type, recipient, context, skip_email, channel_layer):
    try:
        payload = build_notification_payload(event_type, recipient, context)
    except KeyError:
        logger.error('dispatch: no builder for event_type=%s', event_type)
        return

    dedup_key = payload.get('deduplication_key')

    try:
        if dedup_key:
            notification, created = Notification.objects.get_or_create(
                deduplication_key=dedup_key,
                defaults={
                    'recipient': recipient,
                    'event_type': event_type,
                    'title': payload['title'],
                    'body': payload['body'],
                    'data': payload.get('data', {}),
                },
            )
        else:
            notification = Notification.objects.create(
                recipient=recipient,
                event_type=event_type,
                title=payload['title'],
                body=payload['body'],
                data=payload.get('data', {}),
            )
            created = True
    except IntegrityError:
        logger.warning(
            'dispatch: duplicate notification skipped event_type=%s recipient=%s dedup_key=%s',
            event_type, recipient.id, dedup_key,
        )
        return

    if not created:
        return

    _push_ws(channel_layer, notification)

    if not skip_email:
        _enqueue_email(notification)


def _push_ws(channel_layer, notification: Notification) -> None:
    if channel_layer is None:
        return
    group_name = f'notifications_user_{notification.recipient_id}'
    notification_data = NotificationSerializer(notification).data
    notification_data['recipient_id'] = notification.recipient_id
    try:
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                'type': 'notification.push',
                'notification': notification_data,
            },
        )
    except Exception:
        logger.warning(
            'WS push failed for notification %s (user offline or channel layer error)', notification.id
        )


def _enqueue_email(notification: Notification) -> None:
    try:
        if not get_email_preference(notification.recipient, notification.event_type):
            return
    except Exception:
        logger.warning('get_email_preference failed for notification %s', notification.id)
        return

    def _send():
        from notifications.tasks import send_notification_email_task
        send_notification_email_task.delay(notification.pk)

    try:
        transaction.on_commit(_send)
    except Exception:
        logger.warning(
            'transaction.on_commit failed for email enqueue (notification %s), calling directly',
            notification.id,
        )
        _send()
