import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified
from id_verification.models import IdentityVerification
from id_verification.serializers import (
    VerificationCreateSerializer,
    VerificationDetailSerializer,
    VerificationUpdateSerializer,
)

logger = logging.getLogger(__name__)


def _require_instructor(request):
    if request.user.user_type != 'instructor':
        return Response(
            {'success': False, 'message': 'Only instructors can access this resource.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _check_profile_completeness(user):
    """
    Check if instructor profile is complete.
    Returns (is_complete: bool, missing_fields: list).
    """
    try:
        profile = user.instructor_profile
    except Exception:
        return False, ['Profile does not exist. Please create your profile first.']

    required_fields = {
        'headline': 'Headline is required.',
        'bio': 'Bio is required.',
        'specialization': 'At least one specialization is required.',
        'years_of_experience': 'Years of experience is required.',
        'current_title': 'Current title is required.',
    }
    missing = {}

    for field, message in required_fields.items():
        value = getattr(profile, field, None)
        if field == 'years_of_experience':
            if value is None or value <= 0:
                missing[field] = message
        elif isinstance(value, list):
            if len(value) == 0:
                missing[field] = message
        elif not value or (isinstance(value, str) and not value.strip()):
            missing[field] = message

    return len(missing) == 0, missing


class VerificationCreateView(APIView):
    """
    POST → Create a new draft verification request.
    All document fields are optional at this stage.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request):
        err = _require_instructor(request)
        if err:
            return err

        serializer = VerificationCreateSerializer(
            data=request.data, context={'request': request},
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        verification = serializer.save(user=request.user)
        return Response(
            {
                'success': True,
                'message': 'Draft verification created.',
                'data': VerificationDetailSerializer(verification).data,
            },
            status=status.HTTP_201_CREATED,
        )


class VerificationUpdateView(APIView):
    """
    PUT / PATCH → Update a draft or action_required verification request.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def _get_editable(self, request, pk):
        return get_object_or_404(
            IdentityVerification,
            pk=pk,
            user=request.user,
            status__in=('draft', 'action_required'),
        )

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        err = _require_instructor(request)
        if err:
            return err

        verification = self._get_editable(request, pk)
        serializer = VerificationUpdateSerializer(
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
                'data': VerificationDetailSerializer(verification).data,
            },
            status=status.HTTP_200_OK,
        )


class VerificationSubmitView(APIView):
    """
    POST → Submit a draft verification (draft → submitted).
    Also handles resubmission (action_required → submitted).
    Validates that all required fields are filled.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request, pk):
        err = _require_instructor(request)
        if err:
            return err

        # Check if instructor profile is complete
        is_complete, missing_fields = _check_profile_completeness(request.user)
        if not is_complete:
            return Response(
                {
                    'success': False,
                    'message': 'Your profile must be complete before submitting for verification.',
                    'errors': {'profile': missing_fields},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        verification = get_object_or_404(
            IdentityVerification,
            pk=pk,
            user=request.user,
            status__in=('draft', 'action_required'),
        )

        try:
            verification.transition_to('submitted')
        except Exception as e:
            return Response(
                {'success': False, 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'success': True,
                'message': 'Verification submitted successfully.',
                'data': VerificationDetailSerializer(verification).data,
            },
            status=status.HTTP_200_OK,
        )


class VerificationListView(APIView):
    """
    GET → Instructor views their verification history (most recent first).
    """
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        err = _require_instructor(request)
        if err:
            return err

        verifications = IdentityVerification.objects.filter(user=request.user)
        serializer = VerificationDetailSerializer(verifications, many=True)
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK,
        )


class VerificationDetailView(APIView):
    """
    GET → Instructor views a single verification request.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request, pk):
        err = _require_instructor(request)
        if err:
            return err

        verification = get_object_or_404(
            IdentityVerification, pk=pk, user=request.user,
        )
        serializer = VerificationDetailSerializer(verification)
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK,
        )
