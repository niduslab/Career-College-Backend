import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
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


def _require_admin(request):
    if not request.user.is_staff:
        return Response(
            {'success': False, 'message': 'Admin access required.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


class AdminVerificationListView(APIView):
    """
    GET → Admin lists all verification requests.
    Supports ?status= filter and pagination.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        err = _require_admin(request)
        if err:
            return err

        queryset = IdentityVerification.objects.select_related('user').all()
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = AdminVerificationListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AdminVerificationDetailView(APIView):
    """
    GET → Admin views full detail of a verification request.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        err = _require_admin(request)
        if err:
            return err

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
    """
    POST → Admin performs a review action on a verification request.

    Actions: pick_up, approve, reject, request_action, expire.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        err = _require_admin(request)
        if err:
            return err

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
        except Exception as e:
            return Response(
                {'success': False, 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        detail = AdminVerificationDetailSerializer(
            IdentityVerification.objects.select_related('user', 'reviewed_by').get(pk=pk),
        )
        return Response(
            {'success': True, 'message': f'Verification {target_status}.', 'data': detail.data},
            status=status.HTTP_200_OK,
        )
