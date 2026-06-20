import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import InstructorProfile
from authentication.serializers import (
    ExpertCreateSerializer,
    ExpertListSerializer,
    ExpertUpdateSerializer,
)
from authentication.services.expert_service import (
    ExpertError,
    get_institution_expert,
    institution_experts_qs,
    provision_expert,
    set_expert_active,
    update_expert,
)
from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsVerifiedPartnerInstitution

logger = logging.getLogger(__name__)


class InstitutionExpertListCreateView(APIView):
    """
    GET  /api/v1/auth/partner/experts/   → list this institution's experts.
    POST /api/v1/auth/partner/experts/   → onboard a new expert (auto-provision).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def get(self, request):
        institution = request.user.partner_institution_profile
        queryset = institution_experts_qs(institution)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ExpertListSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response

    def post(self, request):
        institution = request.user.partner_institution_profile
        serializer = ExpertCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        try:
            profile = provision_expert(
                institution,
                full_name=data['full_name'],
                email=data['email'],
                bio=data.get('bio', ''),
                headline=data.get('headline', ''),
                specialization=data.get('specialization', []),
            )
        except ExpertError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Expert provisioning failed for institution %s', institution.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Expert onboarded. An activation email has been sent.',
                'data': ExpertListSerializer(profile).data,
            },
            status=status.HTTP_201_CREATED,
        )


class InstitutionExpertDetailView(APIView):
    """
    GET   /api/v1/auth/partner/experts/<id>/   → expert detail.
    PATCH /api/v1/auth/partner/experts/<id>/   → edit profile / activate / deactivate.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def _get(self, request, expert_id):
        # Numeric id → 404 on no-access (never leak existence).
        return get_institution_expert(request.user.partner_institution_profile, expert_id)

    def get(self, request, expert_id):
        try:
            profile = self._get(request, expert_id)
        except InstructorProfile.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Expert not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': ExpertListSerializer(profile).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, expert_id):
        try:
            profile = self._get(request, expert_id)
        except InstructorProfile.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Expert not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ExpertUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        try:
            update_expert(
                profile,
                bio=data.get('bio'),
                headline=data.get('headline'),
                specialization=data.get('specialization'),
            )
            if 'is_active' in data:
                set_expert_active(
                    request.user.partner_institution_profile, profile, data['is_active'],
                )
        except Exception:
            logger.exception('Expert update failed for profile %s', profile.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Expert updated.',
                'data': ExpertListSerializer(profile).data,
            },
            status=status.HTTP_200_OK,
        )
