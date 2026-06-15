import logging

from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.conf import settings

from .email_utils import render_notification_email

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
    queue='notifications',
)
def send_notification_email_task(self, notification_pk: int) -> None:
    from .models import Notification
    try:
        notification = Notification.objects.select_related('recipient').get(pk=notification_pk)
    except Notification.DoesNotExist:
        logger.warning('send_notification_email_task: notification %s not found', notification_pk)
        return

    recipient = notification.recipient
    if not recipient.is_active or getattr(recipient, 'is_deleted', False):
        logger.info(
            'send_notification_email_task: recipient %s inactive/deleted, skipping', recipient.id
        )
        return

    subject, html_body, text_body = render_notification_email(notification)
    if not subject:
        logger.debug(
            'send_notification_email_task: no template for event_type=%s, skipping',
            notification.event_type,
        )
        return

    from_email = settings.DEFAULT_FROM_EMAIL
    to_email = recipient.email
    try:
        msg = EmailMultiAlternatives(subject=subject, body=text_body, from_email=from_email, to=[to_email])
        msg.attach_alternative(html_body, 'text/html')
        msg.send()
        logger.info(
            'Notification email sent: event_type=%s notification_pk=%s to=%s',
            notification.event_type, notification_pk, to_email,
        )
    except Exception as exc:
        logger.error(
            'Notification email failed: notification_pk=%s to=%s error=%s',
            notification_pk, to_email, exc,
        )
        raise


@shared_task(queue='notifications')
def purge_old_notifications_task(days: int = 90) -> int:
    """Delete notifications older than `days` days. Returns count deleted."""
    from django.utils import timezone
    from datetime import timedelta
    from .models import Notification

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = Notification.objects.filter(created_at__lt=cutoff, is_read=True).delete()
    logger.info('purge_old_notifications_task: deleted %d notifications older than %d days', deleted, days)
    return deleted
