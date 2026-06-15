import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsPlatformAdmin
from id_verification.models import IdentityVerification
from id_verification.serializers import (
    AdminReviewSerializer,
    AdminVerificationDetailSerializer,
    AdminVerificationListSerializer,
)

logger = logging.getLogger(__name__)

ACTION_TO_STATUS = {
    'pick_up': 'under_review',
    'approve': 'approved',
    'reject': 'rejected',
    'request_action': 'action_required',
    'expire': 'expired',
}


class AdminVerificationListView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]

    def get(self, request):
        queryset = IdentityVerification.objects.select_related('user').all()
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = AdminVerificationListSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {
            'success': True,
            'data': paginated_response.data,
        }
        return paginated_response


class AdminVerificationDetailView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]

    def get(self, request, pk):
        verification = get_object_or_404(
            IdentityVerification.objects.select_related('user', 'reviewed_by'),
            pk=pk,
        )
        serializer = AdminVerificationDetailSerializer(verification)
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK,
        )


class AdminVerificationReviewView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]

    def post(self, request, pk):
        verification = get_object_or_404(IdentityVerification, pk=pk)
        serializer = AdminReviewSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = serializer.validated_data['action']
        target_status = ACTION_TO_STATUS[action]
        rejection_reason = serializer.validated_data.get('rejection_reason', '')
        action_required_reason = serializer.validated_data.get('action_required_reason', '')
        admin_notes = serializer.validated_data.get('admin_notes', '')

        try:
            verification.transition_to(
                target_status,
                reviewer=request.user,
                rejection_reason=rejection_reason,
                action_required_reason=action_required_reason,
                admin_notes=admin_notes,
            )
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Action failed.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception:
            logger.exception('Unexpected error during verification transition pk=%s', pk)
            return Response(
                {'success': False, 'message': 'An unexpected server error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        _target_status = target_status
        _verification_user = verification.user
        _action_note = action_required_reason

        def _notify_verification_decision():
            from notifications.models import NotificationEventType
            from notifications.services.dispatcher import dispatch
            if _target_status == 'approved':
                dispatch(NotificationEventType.VERIFICATION_APPROVED, [_verification_user], context={})
            elif _target_status == 'rejected':
                dispatch(NotificationEventType.VERIFICATION_REJECTED, [_verification_user], context={})
            elif _target_status == 'action_required':
                dispatch(
                    NotificationEventType.VERIFICATION_ACTION_REQ,
                    [_verification_user],
                    context={'admin_note': _action_note},
                )

        transaction.on_commit(_notify_verification_decision)

        detail = AdminVerificationDetailSerializer(
            IdentityVerification.objects.select_related('user', 'reviewed_by').get(pk=pk),
        )
        return Response(
            {'success': True, 'message': f'Verification {target_status}.', 'data': detail.data},
            status=status.HTTP_200_OK,
        )
