import logging

from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from authentication.authentication import CookieJWTAuthentication
from core.permissions import IsEmailVerified, IsPlatformAdmin

logger = logging.getLogger(__name__)


class AdminConsoleAPIView(APIView):
    """
    Base view for every authenticated admin-console endpoint.

    Session-primary with a JWT fallback: browser clients authenticate via the
    session cookie (CSRF enforced by ``SessionAuthentication`` on unsafe
    methods), while automated tooling can still present a ``Bearer`` token or
    the JWT cookie. Session auth is enabled here per-view and is deliberately
    NOT added to the global DRF auth classes, so the rest of the API keeps
    working without CSRF tokens.
    """

    authentication_classes = [
        SessionAuthentication,
        CookieJWTAuthentication,
        JWTAuthentication,
    ]
    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._touch_admin_session(request)

    def _touch_admin_session(self, request):
        """
        Best-effort refresh of the current AdminSession's ``last_seen_at`` so
        the device list shows real activity. Never breaks the request.
        """
        session_key = getattr(getattr(request, 'session', None), 'session_key', None)
        if not session_key:
            return
        try:
            from django.utils import timezone

            from admin_console.all_models import AdminSession

            AdminSession.objects.filter(session_key=session_key).update(
                last_seen_at=timezone.now()
            )
        except Exception:
            logger.warning('Failed to touch admin session last_seen_at.')
