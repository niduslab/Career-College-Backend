import logging

from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import (
    Education,
    InstructorProfile,
    LearnerProfile,
    PartnerInstitutionProfile,
    User,
    WorkExperience,
)
from authentication.serializers import (
    EducationSerializer,
    InstructorListSerializer,
    InstructorProfileSerializer,
    InstitutionListSerializer,
    LearnerListSerializer,
    LearnerProfileSerializer,
    PartnerInstitutionProfileSerializer,
    PublicInstructorProfileSerializer,
    PublicLearnerProfileSerializer,
    PublicPartnerInstitutionProfileSerializer,
    UserBasicSerializer,
    WorkExperienceSerializer,
)
from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsProfileOwner

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────

PROFILE_MAP = {
    'learner': ('learner_profile', LearnerProfileSerializer),
    'instructor': ('instructor_profile', InstructorProfileSerializer),
    'partner_institution': ('partner_institution_profile', PartnerInstitutionProfileSerializer),
}


def _get_profile(user):
    """Return (profile_instance, serializer_class) for the given user."""
    entry = PROFILE_MAP.get(user.user_type)
    if entry is None:
        return None, None
    attr, serializer_cls = entry
    try:
        profile = getattr(user, attr)
    except ObjectDoesNotExist:
        profile = None
    return profile, serializer_cls


# ── My Profile (GET / PUT / PATCH) ──────────────────────────

class MyProfileView(APIView):
    """
    Authenticated user manages their own profile.
    GET  → retrieve profile with user info, education and work experience.
    PUT / PATCH → update profile fields only.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        user = request.user
        profile, serializer_cls = _get_profile(user)

        if profile is None:
            return Response(
                {'success': False, 'message': 'Profile not found for your account type.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = {
            'user': UserBasicSerializer(user).data,
            'profile': serializer_cls(profile).data,
        }

        # Education & work experience are available for learners and instructors.
        if user.user_type in ('learner', 'instructor'):
            data['education'] = EducationSerializer(
                user.education_history.all(), many=True
            ).data
            data['work_experience'] = WorkExperienceSerializer(
                user.work_history.all(), many=True
            ).data

        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)

    def put(self, request):
        return self._update(request, partial=False)

    def patch(self, request):
        return self._update(request, partial=True)

    def _update(self, request, partial):
        user = request.user
        profile, serializer_cls = _get_profile(user)

        if profile is None:
            return Response(
                {'success': False, 'message': 'Profile not found for your account type.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = serializer_cls(profile, data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            serializer.save()
        except Exception:
            logger.exception('Failed to update profile for user %s', user.pk)
            return Response(
                {'success': False, 'message': 'Failed to update profile. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Profile updated successfully.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )


# ── Education CRUD ───────────────────────────────────────────

class EducationListCreateView(APIView):
    """
    GET  → list the authenticated user's education entries.
    POST → create a new education entry for the authenticated user.
    Allowed for learners and instructors only.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        if request.user.user_type not in ('learner', 'instructor'):
            return Response(
                {'success': False, 'message': 'Education entries are only available for learners and instructors.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        entries = request.user.education_history.all()
        serializer = EducationSerializer(entries, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        if request.user.user_type not in ('learner', 'instructor'):
            return Response(
                {'success': False, 'message': 'Education entries are only available for learners and instructors.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = EducationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            serializer.save(user=request.user)
        except Exception:
            logger.exception('Failed to create education entry for user %s', request.user.pk)
            return Response(
                {'success': False, 'message': 'Failed to create education entry. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Education entry created.', 'data': serializer.data},
            status=status.HTTP_201_CREATED,
        )


class EducationDetailView(APIView):
    """
    GET / PUT / PATCH / DELETE a single education entry.
    Only the owning user can access.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified, IsProfileOwner]

    def _get_object(self, request, pk):
        obj = get_object_or_404(Education, pk=pk, user=request.user)
        self.check_object_permissions(request, obj)
        return obj

    def get(self, request, pk):
        obj = self._get_object(request, pk)
        return Response(
            {'success': True, 'data': EducationSerializer(obj).data},
            status=status.HTTP_200_OK,
        )

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        obj = self._get_object(request, pk)
        serializer = EducationSerializer(obj, data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            serializer.save()
        except Exception:
            logger.exception('Failed to update education entry pk=%s', obj.pk)
            return Response(
                {'success': False, 'message': 'Failed to update education entry. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Education entry updated.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        obj = self._get_object(request, pk)
        try:
            obj.delete()
        except Exception:
            logger.exception('Failed to delete education entry pk=%s', obj.pk)
            return Response(
                {'success': False, 'message': 'Failed to delete education entry. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Education entry deleted.'},
            status=status.HTTP_200_OK,
        )


# ── Work Experience CRUD ─────────────────────────────────────

class WorkExperienceListCreateView(APIView):
    """
    GET  → list the authenticated user's work experience entries.
    POST → create a new work experience entry for the authenticated user.
    Allowed for learners and instructors only.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        if request.user.user_type not in ('learner', 'instructor'):
            return Response(
                {'success': False, 'message': 'Work experience entries are only available for learners and instructors.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        entries = request.user.work_history.all()
        serializer = WorkExperienceSerializer(entries, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request):
        if request.user.user_type not in ('learner', 'instructor'):
            return Response(
                {'success': False, 'message': 'Work experience entries are only available for learners and instructors.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = WorkExperienceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            serializer.save(user=request.user)
        except Exception:
            logger.exception('Failed to create work experience entry for user %s', request.user.pk)
            return Response(
                {'success': False, 'message': 'Failed to create work experience entry. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Work experience entry created.', 'data': serializer.data},
            status=status.HTTP_201_CREATED,
        )


class WorkExperienceDetailView(APIView):
    """
    GET / PUT / PATCH / DELETE a single work experience entry.
    Only the owning user can access.
    """
    permission_classes = [IsAuthenticated, IsEmailVerified, IsProfileOwner]

    def _get_object(self, request, pk):
        obj = get_object_or_404(WorkExperience, pk=pk, user=request.user)
        self.check_object_permissions(request, obj)
        return obj

    def get(self, request, pk):
        obj = self._get_object(request, pk)
        return Response(
            {'success': True, 'data': WorkExperienceSerializer(obj).data},
            status=status.HTTP_200_OK,
        )

    def put(self, request, pk):
        return self._update(request, pk, partial=False)

    def patch(self, request, pk):
        return self._update(request, pk, partial=True)

    def _update(self, request, pk, partial):
        obj = self._get_object(request, pk)
        serializer = WorkExperienceSerializer(obj, data=request.data, partial=partial)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            serializer.save()
        except Exception:
            logger.exception('Failed to update work experience entry pk=%s', obj.pk)
            return Response(
                {'success': False, 'message': 'Failed to update work experience entry. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Work experience entry updated.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        obj = self._get_object(request, pk)
        try:
            obj.delete()
        except Exception:
            logger.exception('Failed to delete work experience entry pk=%s', obj.pk)
            return Response(
                {'success': False, 'message': 'Failed to delete work experience entry. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Work experience entry deleted.'},
            status=status.HTTP_200_OK,
        )


# ── Public Profile Detail (by slug) ─────────────────────────

class PublicProfileDetailView(APIView):
    """
    GET /profiles/<slug>/
    Returns the public profile for any user type.
    Partner institutions are resolved by their own institution slug
    (PartnerInstitutionProfile.slug); every other user type is looked up by
    User.name_slug.
    Only returns data for active, non-deleted, email-verified users.
    For learners, the profile must also be marked public.
    """
    permission_classes = [AllowAny]

    def get(self, request, slug):
        institution = (
            PartnerInstitutionProfile.objects
            .select_related('user')
            .filter(
                slug=slug,
                is_active=True,
                user__is_active=True,
                user__is_email_verified=True,
                user__is_deleted=False,
            )
            .first()
        )
        if institution is not None:
            data = PublicPartnerInstitutionProfileSerializer(institution).data
            data['user_type'] = institution.user.user_type
            return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)

        user = User.objects.filter(
            name_slug=slug,
            is_active=True,
            is_email_verified=True,
        ).first()

        if user is None:
            return Response(
                {'success': False, 'message': 'Profile not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if user.user_type == 'learner':
            try:
                profile = user.learner_profile
            except ObjectDoesNotExist:
                profile = None
            if profile is None or not profile.is_profile_public:
                return Response(
                    {'success': False, 'message': 'Profile not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            data = PublicLearnerProfileSerializer(profile).data
        elif user.user_type == 'instructor':
            try:
                profile = user.instructor_profile
            except ObjectDoesNotExist:
                profile = None
            if profile is None:
                return Response(
                    {'success': False, 'message': 'Profile not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            data = PublicInstructorProfileSerializer(profile).data
        elif user.user_type == 'partner_institution':
            try:
                profile = user.partner_institution_profile
            except ObjectDoesNotExist:
                profile = None
            if profile is None or not profile.is_active:
                return Response(
                    {'success': False, 'message': 'Profile not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            data = PublicPartnerInstitutionProfileSerializer(profile).data
        else:
            return Response(
                {'success': False, 'message': 'Profile not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        data['user_type'] = user.user_type
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)


# ── Public Profile Lists (paginated) ────────────────────────

class _PaginatedListMixin:
    """Helper to paginate querysets in plain APIViews."""

    def paginate(self, request, queryset, serializer_cls):
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = serializer_cls(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {
            'success': True,
            'data': paginated_response.data,
        }
        return paginated_response


class PublicLearnerListView(_PaginatedListMixin, APIView):
    """
    GET /profiles/learners/
    Browse public learner profiles. Supports ?country= and ?experience_level= filters.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        qs = LearnerProfile.objects.select_related('user').filter(
            is_profile_public=True,
            user__is_active=True,
            user__is_email_verified=True,
            user__is_deleted=False,
        )

        country = request.query_params.get('country')
        if country:
            qs = qs.filter(country__iexact=country)
        experience_level = request.query_params.get('experience_level')
        if experience_level:
            qs = qs.filter(experience_level=experience_level)

        return self.paginate(request, qs, LearnerListSerializer)


class PublicInstructorListView(_PaginatedListMixin, APIView):
    """
    GET /profiles/instructors/
    Browse instructor profiles. Supports ?country= and ?is_verified= filters.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        qs = InstructorProfile.objects.select_related('user').filter(
            user__is_active=True,
            user__is_email_verified=True,
            user__is_deleted=False,
        )

        country = request.query_params.get('country')
        if country:
            qs = qs.filter(country__iexact=country)
        is_verified = request.query_params.get('is_verified')
        if is_verified is not None:
            qs = qs.filter(is_verified=is_verified.strip().lower() in ('true', '1'))

        return self.paginate(request, qs, InstructorListSerializer)


class PublicInstitutionListView(_PaginatedListMixin, APIView):
    """
    GET /profiles/institutions/
    Browse partner institution profiles. Supports ?country= and ?institution_type= filters.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        qs = PartnerInstitutionProfile.objects.select_related('user').filter(
            is_active=True,
            user__is_active=True,
            user__is_email_verified=True,
            user__is_deleted=False,
        )

        country = request.query_params.get('country')
        if country:
            qs = qs.filter(country__iexact=country)
        institution_type = request.query_params.get('institution_type')
        if institution_type:
            qs = qs.filter(institution_type=institution_type)

        return self.paginate(request, qs, InstitutionListSerializer)
