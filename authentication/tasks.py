import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
)
def send_expert_credentials_email_task(self, user_pk, password, institution_name=None):
    """
    Email an institution-onboarded expert their login credentials.

    The plaintext password is passed as a task argument (it is never persisted
    in the DB — only its hash is). It therefore transits the broker; keep the
    broker trusted and the result backend short-lived.
    """
    from authentication.models import User
    from authentication.utils import send_credentials_email

    try:
        user = User.objects.get(pk=user_pk)
    except User.DoesNotExist:
        logger.warning('send_expert_credentials_email_task: user %s not found', user_pk)
        return

    sent = send_credentials_email(user, password, institution_name)
    if not sent:
        # send_credentials_email logs the cause; raise so the task retries.
        raise RuntimeError(f'Credentials email send failed for user {user_pk}')


@shared_task(
    bind=True,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    # OTP codes expire in 2 minutes, so keep retries tight — a delivery that
    # lands after expiry is useless and the user resends instead.
    retry_backoff_max=30,
    max_retries=2,
)
def send_otp_email_task(self, user_pk, otp_code, purpose='registration'):
    """Send an OTP email asynchronously. `otp_code` is read at enqueue time so a
    later regeneration doesn't change what this task delivers."""
    from authentication.models import User
    from authentication.utils import send_otp_email

    try:
        user = User.objects.get(pk=user_pk)
    except User.DoesNotExist:
        logger.warning('send_otp_email_task: user %s not found', user_pk)
        return

    sent = send_otp_email(user, otp_code, purpose=purpose)
    if not sent:
        # send_otp_email logs the cause; raise so the task retries.
        raise RuntimeError(f'OTP email send failed for user {user_pk} purpose={purpose}')
