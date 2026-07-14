import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from admin_console.all_models import AdminSession

logger = logging.getLogger(__name__)


def _is_admin(user):
    """Admin gate — matches core.permissions.IsPlatformAdmin."""
    return bool(getattr(user, 'is_staff', False) or getattr(user, 'user_type', None) == 'admin')


def _client_ip(request):
    """
    Best-effort client IP. Prefers the first X-Forwarded-For hop when present
    (proxy-dependent — trust it only behind a proxy that sets it), else the
    direct REMOTE_ADDR.
    """
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


@receiver(user_logged_in)
def record_admin_session(sender, request, user, **kwargs):
    """
    Capture an admin's device/session on login. No-op for non-admins and for
    logins without a server-side session (JWT clients never call django_login).
    Idempotent per session_key via update_or_create.
    """
    if request is None or not _is_admin(user):
        return

    session_key = getattr(request.session, 'session_key', None)
    if not session_key:
        return

    ua_string = request.META.get('HTTP_USER_AGENT', '')
    browser = os_name = device = ''
    try:
        from user_agents import parse as parse_ua

        ua = parse_ua(ua_string)
        browser = (f'{ua.browser.family} {ua.browser.version_string}').strip()[:128]
        os_name = (f'{ua.os.family} {ua.os.version_string}').strip()[:128]
        device = (ua.device.family or '')[:128]
    except Exception:
        # Parsing must never block login; raw user_agent is still stored.
        logger.warning('User-agent parse failed for admin session; storing raw only.')

    try:
        AdminSession.objects.update_or_create(
            session_key=session_key,
            defaults={
                'user': user,
                'ip_address': _client_ip(request),
                'user_agent': ua_string,
                'browser': browser,
                'os': os_name,
                'device': device,
            },
        )
    except Exception:
        logger.exception('Failed to record admin session for user_id=%s', user.pk)


@receiver(user_logged_out)
def clear_admin_session(sender, request, user, **kwargs):
    """Drop the AdminSession row when its session ends (explicit logout)."""
    if request is None:
        return
    session_key = getattr(request.session, 'session_key', None)
    if not session_key:
        return
    try:
        AdminSession.objects.filter(session_key=session_key).delete()
    except Exception:
        logger.exception('Failed to clear admin session on logout.')
