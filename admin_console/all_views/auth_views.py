import logging

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from admin_console.all_views.base import AdminConsoleAPIView
from admin_console.serializers import AdminLoginSerializer

logger = logging.getLogger(__name__)

_ADMIN_LOGIN_RATE_LIMIT = getattr(settings, 'ADMIN_LOGIN_RATE_LIMIT', '10/min')

# Session key holding the login timestamp, used by the idle/re-auth helpers.
ADMIN_LOGIN_AT_SESSION_KEY = 'admin_login_at'


class AdminLoginThrottle(AnonRateThrottle):
    scope = 'admin_login'
    rate = _ADMIN_LOGIN_RATE_LIMIT


def _is_admin(user):
    """Admin gate: matches core.permissions.IsPlatformAdmin."""
    return bool(user.is_staff or user.user_type == 'admin')


def _admin_payload(user):
    return {
        'user_id': user.pk,
        'email': user.email,
        'full_name': user.full_name,
        'user_type': user.user_type,
        'is_staff': user.is_staff,
    }


class CsrfTokenView(APIView):
    """
    Issue a CSRF cookie so the SPA can send ``X-CSRFToken`` on session POSTs.

    Public and session-free: no CSRF is enforced here (there is nothing to
    protect yet), it only primes the ``csrftoken`` cookie for later writes.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        get_token(request)  # forces CsrfViewMiddleware to set the cookie
        return Response(
            {'success': True, 'message': 'CSRF cookie set.'},
            status=status.HTTP_200_OK,
        )


class AdminLoginView(APIView):
    """
    Establish a server-side admin session from email + password.

    No CSRF is enforced (no session yet); credentials plus the login throttle
    are the protection. Valid non-admin credentials → 403; bad credentials →
    generic 400 (no account enumeration).
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AdminLoginThrottle]

    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Login failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = serializer.validated_data['user']

        if not _is_admin(user):
            return Response(
                {'success': False, 'message': 'Only administrators can sign in here.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            # login() rotates the session key (fixation-safe) and persists the
            # authenticated user against the session.
            django_login(request, user)
            request.session[ADMIN_LOGIN_AT_SESSION_KEY] = timezone.now().timestamp()
        except Exception:
            logger.exception('Admin session login failed for user_id=%s', user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected server error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Login successful.', 'data': _admin_payload(user)},
            status=status.HTTP_200_OK,
        )


class AdminLogoutView(AdminConsoleAPIView):
    """Flush the admin session. CSRF-protected (session auth)."""

    def post(self, request):
        django_logout(request)
        return Response(
            {'success': True, 'message': 'Logged out.'},
            status=status.HTTP_200_OK,
        )


class AdminSessionView(AdminConsoleAPIView):
    """Who-am-I / liveness check for the SPA to confirm the session is alive."""

    def get(self, request):
        data = _admin_payload(request.user)
        data['idle_timeout_seconds'] = getattr(settings, 'ADMIN_SESSION_IDLE_TIMEOUT', 1800)
        return Response(
            {'success': True, 'data': data},
            status=status.HTTP_200_OK,
        )
