import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified

from .models import Notification, NotificationCategory, NotificationPreference
from .serializers import (
    MarkReadSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    PreferencePatchSerializer,
)

logger = logging.getLogger(__name__)


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        qs = (
            Notification.objects
            .filter(recipient=request.user)
            .select_related('recipient')
        )
        event_type = request.query_params.get('event_type')
        if event_type:
            qs = qs.filter(event_type=event_type)
        is_read = request.query_params.get('is_read')
        if is_read is not None:
            qs = qs.filter(is_read=is_read.lower() == 'true')

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = NotificationSerializer(page, many=True)
        paginated = paginator.get_paginated_response(serializer.data)
        paginated.data = {'success': True, 'data': paginated.data}
        return paginated


class MarkReadView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request):
        serializer = MarkReadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        validated = serializer.validated_data
        try:
            qs = Notification.objects.filter(recipient=request.user, is_read=False)
            if not validated.get('all'):
                qs = qs.filter(id__in=validated['ids'])
            qs.update(is_read=True, read_at=timezone.now())
        except Exception as e:
            logger.error('MarkReadView failed for user %s: %s', request.user.id, e)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'success': True, 'message': 'Marked as read.'})


class UnreadCountView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({'success': True, 'data': {'count': count}})


class NotificationPreferenceView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        prefs = []
        for category in NotificationCategory.values:
            pref, _ = NotificationPreference.objects.get_or_create(
                user=request.user,
                category=category,
                defaults={'email_enabled': True, 'push_enabled': True},
            )
            prefs.append(pref)
        return Response({
            'success': True,
            'data': NotificationPreferenceSerializer(prefs, many=True).data,
        })

    def patch(self, request):
        serializer = PreferencePatchSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        updates = serializer.validated_data
        try:
            for category, fields in updates.items():
                NotificationPreference.objects.update_or_create(
                    user=request.user,
                    category=category,
                    defaults=fields,
                )
        except Exception as e:
            logger.error('NotificationPreferenceView PATCH failed for user %s: %s', request.user.id, e)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        prefs = list(
            NotificationPreference.objects.filter(user=request.user).order_by('category')
        )
        return Response({
            'success': True,
            'message': 'Preferences updated.',
            'data': NotificationPreferenceSerializer(prefs, many=True).data,
        })
