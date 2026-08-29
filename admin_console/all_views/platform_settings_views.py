"""
Platform settings endpoints.

Routes (under /api/v1/admin-console/):
    GET   platform-settings/   -> PlatformSettingsView
    PATCH platform-settings/   -> PlatformSettingsView
"""

import logging

from django.db import transaction
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from admin_console.all_models.platform_settings_models import PlatformSettings
from admin_console.all_models.user_admin_models import AdminActionLog
from admin_console.all_serializers.platform_settings_serializers import (
    PlatformSettingsSerializer,
)
from admin_console.all_views.base import AdminConsoleAPIView
from admin_console.services.user_admin_service import log_admin_action

logger = logging.getLogger(__name__)


class PlatformSettingsView(AdminConsoleAPIView):
    """
    GET / PATCH /api/v1/admin-console/platform-settings/

    Platform branding and the default authorized signatory used on certificates
    for courses that have no institution signatory of their own.

    Changing the signatory here only affects certificates issued from now on —
    every issued certificate carries its own frozen copy.
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        settings_obj = PlatformSettings.load()
        return Response(
            {
                'success': True,
                'message': 'Platform settings retrieved.',
                'data': PlatformSettingsSerializer(settings_obj).data,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request):
        settings_obj = PlatformSettings.load()
        serializer = PlatformSettingsSerializer(
            settings_obj, data=request.data, partial=True
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        changed = sorted(serializer.validated_data.keys())
        try:
            with transaction.atomic():
                updated = serializer.save()
                log_admin_action(
                    actor=request.user,
                    action=AdminActionLog.Action.PLATFORM_SETTINGS_UPDATE,
                    target=None,
                    metadata={'changed_fields': changed},
                )
        except Exception:
            logger.exception('Platform settings update failed for user %s', request.user.id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Platform settings updated.',
                'data': PlatformSettingsSerializer(updated).data,
            },
            status=status.HTTP_200_OK,
        )
