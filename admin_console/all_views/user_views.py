import logging

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from admin_console.all_models import AdminActionLog
from admin_console.all_serializers import (
    AdminActionLogSerializer,
    AdminUserDetailSerializer,
    AdminUserListSerializer,
)
from admin_console.all_views.base import AdminConsoleAPIView
from admin_console.services.user_admin_service import (
    AdminUserActionError,
    change_user_role,
    reactivate_user,
    search_users,
    suspend_user,
)
from authentication.models import User
from core.pagination import StandardResultsSetPagination

logger = logging.getLogger(__name__)


class AdminActionThrottle(UserRateThrottle):
    """Per-admin rate limit for the mutating user-management endpoints."""

    scope = 'admin_action'
    rate = getattr(settings, 'ADMIN_ACTION_RATE_LIMIT', '30/min')


def _paginated(request, queryset, serializer_cls):
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = serializer_cls(page, many=True)
    response = paginator.get_paginated_response(serializer.data)
    response.data = {'success': True, 'data': response.data}
    return response


class AdminUserListView(AdminConsoleAPIView):
    """GET — search/filter/sort the platform's user accounts (paginated)."""

    def get(self, request):
        try:
            queryset = search_users(request.query_params)
        except AdminUserActionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        return _paginated(request, queryset, AdminUserListSerializer)


class AdminUserDetailView(AdminConsoleAPIView):
    """GET — one account (including soft-deleted, so admins can inspect them)."""

    def get(self, request, pk):
        try:
            user = User.objects.all_with_deleted().get(pk=pk)
        except User.DoesNotExist:
            return Response(
                {'success': False, 'message': 'User not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': AdminUserDetailSerializer(user).data},
            status=status.HTTP_200_OK,
        )


class AdminUserSuspendView(AdminConsoleAPIView):
    """POST — suspend a user (blocks new logins + existing access tokens)."""

    throttle_classes = [AdminActionThrottle]

    def post(self, request, pk):
        reason = (request.data.get('reason') or '').strip()
        try:
            user = suspend_user(request.user, pk, reason=reason)
        except AdminUserActionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        return Response(
            {'success': True, 'message': 'User suspended.', 'data': AdminUserDetailSerializer(user).data},
            status=status.HTTP_200_OK,
        )


class AdminUserReactivateView(AdminConsoleAPIView):
    """POST — lift a suspension."""

    throttle_classes = [AdminActionThrottle]

    def post(self, request, pk):
        try:
            user = reactivate_user(request.user, pk)
        except AdminUserActionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        return Response(
            {'success': True, 'message': 'User reactivated.', 'data': AdminUserDetailSerializer(user).data},
            status=status.HTTP_200_OK,
        )


class AdminUserRoleView(AdminConsoleAPIView):
    """POST — change user_type and/or grant/revoke admin (is_staff)."""

    throttle_classes = [AdminActionThrottle]

    def post(self, request, pk):
        new_user_type = request.data.get('user_type')
        is_staff = request.data.get('is_staff')
        try:
            user = change_user_role(
                request.user, pk,
                new_user_type=new_user_type,
                is_staff=is_staff,
            )
        except AdminUserActionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        return Response(
            {'success': True, 'message': 'Role updated.', 'data': AdminUserDetailSerializer(user).data},
            status=status.HTTP_200_OK,
        )


class AdminAuditLogListView(AdminConsoleAPIView):
    """GET — the admin-action audit log (paginated); filter by target/actor/action."""

    def get(self, request):
        queryset = AdminActionLog.objects.select_related('actor', 'target_user').all()

        target_id = request.query_params.get('target_user_id')
        if target_id:
            queryset = queryset.filter(target_user_id=target_id)
        actor_id = request.query_params.get('actor_id')
        if actor_id:
            queryset = queryset.filter(actor_id=actor_id)
        action = request.query_params.get('action')
        if action:
            queryset = queryset.filter(action=action)

        return _paginated(request, queryset, AdminActionLogSerializer)
