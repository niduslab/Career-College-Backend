from django.conf import settings
from django.db import models


class AdminSession(models.Model):
    """
    One row per admin browser/device session.

    Mirrors a row in the ``django_session`` table (linked by ``session_key``)
    with the extra device metadata Django does not store: IP, raw user-agent,
    and the parsed browser/os/device. Powers the "your active sessions" list
    and remote logout. Created by the ``user_logged_in`` signal receiver in
    ``admin_console/signals.py``; deleted on logout / remote revoke.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='admin_sessions',
    )
    # Links to the django_session row; deleting that row logs the device out.
    session_key = models.CharField(max_length=40, unique=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    # Denormalized from user_agent at create time (parsing stays swappable).
    browser = models.CharField(max_length=128, blank=True)
    os = models.CharField(max_length=128, blank=True)
    device = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_seen_at']

    def __str__(self):
        return f'AdminSession(user={self.user_id}, key={self.session_key[:8]}…)'
