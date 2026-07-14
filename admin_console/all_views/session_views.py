import logging

from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from admin_console.all_models import AdminSession
from admin_console.all_serializers import AdminSessionSerializer
from admin_console.all_views.base import AdminConsoleAPIView
from core.pagination import StandardResultsSetPagination

logger = logging.getLogger(__name__)


def _live_session_keys(keys):
    """Subset of ``keys`` that still have an unexpired django_session row."""
    if not keys:
        return set()
    return set(
        Session.objects.filter(
            session_key__in=keys, expire_date__gt=timezone.now()
        ).values_list('session_key', flat=True)
    )


def _revoke(session_key):
    """Kill a device: delete the django_session row (logs it out) + our record."""
    SessionStore(session_key=session_key).delete()
    AdminSession.objects.filter(session_key=session_key).delete()


class AdminSessionListView(AdminConsoleAPIView):
    """GET the caller's own active sessions (one row per device/browser)."""

    def get(self, request):
        rows = list(AdminSession.objects.filter(user=request.user))
        live = _live_session_keys([r.session_key for r in rows])

        # Prune records whose underlying session has expired/been deleted.
        stale = [r.session_key for r in rows if r.session_key not in live]
        if stale:
            try:
                AdminSession.objects.filter(session_key__in=stale).delete()
            except Exception:
                logger.warning('Failed to prune stale admin sessions.')

        active = [r for r in rows if r.session_key in live]

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(active, request)
        serializer = AdminSessionSerializer(
            page,
            many=True,
            context={'current_session_key': request.session.session_key},
        )
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class AdminSessionRevokeView(AdminConsoleAPIView):
    """DELETE one own session (numeric id → 404 on no-access, no existence leak)."""

    def delete(self, request, pk):
        try:
            row = AdminSession.objects.get(pk=pk, user=request.user)
        except AdminSession.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Session not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            _revoke(row.session_key)
        except Exception:
            logger.exception('Failed to revoke admin session pk=%s', pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Session revoked.'},
            status=status.HTTP_200_OK,
        )


class AdminSessionRevokeOthersView(AdminConsoleAPIView):
    """POST — log out of every own session except the current one."""

    def post(self, request):
        current = request.session.session_key
        others = AdminSession.objects.filter(user=request.user).exclude(
            session_key=current
        )
        keys = list(others.values_list('session_key', flat=True))

        revoked = 0
        for key in keys:
            try:
                _revoke(key)
                revoked += 1
            except Exception:
                logger.exception('Failed to revoke admin session key during revoke-others.')

        return Response(
            {'success': True, 'message': 'Signed out of other sessions.', 'data': {'revoked': revoked}},
            status=status.HTTP_200_OK,
        )
