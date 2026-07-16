from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

from admin_console.all_views.base import AdminConsoleAPIView

# Session key holding the login timestamp, stamped by the shared login
# (authentication.UserLoginView) and read by IsRecentlyAuthenticatedAdmin.
ADMIN_LOGIN_AT_SESSION_KEY = 'admin_login_at'


def _admin_payload(user):
    return {
        'user_id': user.pk,
        'email': user.email,
        'full_name': user.full_name,
        'user_type': user.user_type,
        'is_staff': user.is_staff,
    }


class AdminSessionView(AdminConsoleAPIView):
    """Who-am-I / liveness check for the SPA to confirm the session is alive."""

    def get(self, request):
        data = _admin_payload(request.user)
        data['idle_timeout_seconds'] = getattr(settings, 'ADMIN_SESSION_IDLE_TIMEOUT', 1800)
        return Response(
            {'success': True, 'data': data},
            status=status.HTTP_200_OK,
        )
