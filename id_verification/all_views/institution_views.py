import logging

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified
from id_verification.models import InstitutionVerification
from id_verification.serializers import (
    InstitutionVerificationCreateSerializer,
    InstitutionVerificationDetailSerializer,
    InstitutionVerificationUpdateSerializer,
)

logger = logging.getLogger(__name__)


def _get_institution(request):
    """Return (profile, error_response). Exactly one is non-None."""
    if request.user.user_type != 'partner_institution':
        return None, Response(
            {'success': False, 'message': 'Only partner institutions can access this resource.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    try:
        return request.user.partner_institution_profile, None
    except ObjectDoesNotExist:
        return None, Response(
            {'success': False, 'message': 'Institution profile does not exist.'},
            status=status.HTTP_404_NOT_FOUND,
        )


class InstitutionVerificationCreateView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request):
        institution, err = _get_institution(request)
        if err:
            return err

        serializer = InstitutionVerificationCreateSerializer(
            data=request.data, context={'institution': institution},
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        verification = serializer.save(institution=institution)
        return Response(
            {
                'success': True,
                'message': 'Draft verification created.',
                'data': InstitutionVerificationDetailSerializer(verification).data,
            },
            status=status.HTTP_201_CREATED,
        )


class InstitutionVerificationUpdateView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        institution, err = _get_institution(request)
        if err:
            return err

        verification = get_object_or_404(
            InstitutionVerification,
            pk=pk,
            institution=institution,
            status__in=('draft', 'action_required'),
        )
        serializer = InstitutionVerificationUpdateSerializer(
            verification, data=request.data, partial=partial,
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer.save()
        return Response(
            {
                'success': True,
                'message': 'Verification updated.',
                'data': InstitutionVerificationDetailSerializer(verification).data,
            },
            status=status.HTTP_200_OK,
        )


class InstitutionVerificationSubmitView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request, pk):
        institution, err = _get_institution(request)
        if err:
            return err

        verification = get_object_or_404(
            InstitutionVerification,
            pk=pk,
            institution=institution,
            status__in=('draft', 'action_required'),
        )

        try:
            verification.transition_to('submitted')
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Submission failed.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception:
            logger.exception('Unexpected error during institution verification submission pk=%s', pk)
            return Response(
                {'success': False, 'message': 'An unexpected server error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        _verification_id = verification.pk
        _institution_name = institution.institution_name

        def _notify_submitted():
            from authentication.models import User
            from notifications.models import NotificationEventType
            from notifications.services.dispatcher import dispatch
            admins = list(User.objects.filter(user_type='admin', is_deleted=False, is_active=True))
            if admins:
                dispatch(
                    NotificationEventType.INST_VERIFICATION_SUBMITTED,
                    admins,
                    context={'verification_id': _verification_id, 'institution_name': _institution_name},
                )

        transaction.on_commit(_notify_submitted)

        return Response(
            {
                'success': True,
                'message': 'Verification submitted successfully.',
                'data': InstitutionVerificationDetailSerializer(verification).data,
            },
            status=status.HTTP_200_OK,
        )


class InstitutionVerificationListView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        institution, err = _get_institution(request)
        if err:
            return err

        verifications = InstitutionVerification.objects.filter(institution=institution)
        serializer = InstitutionVerificationDetailSerializer(verifications, many=True)
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK,
        )


class InstitutionVerificationDetailView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request, pk):
        institution, err = _get_institution(request)
        if err:
            return err

        verification = get_object_or_404(
            InstitutionVerification, pk=pk, institution=institution,
        )
        serializer = InstitutionVerificationDetailSerializer(verification)
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK,
        )
