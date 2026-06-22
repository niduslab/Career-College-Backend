import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import Department, InstructorProfile
from authentication.serializers import (
    DepartmentSerializer,
    ExpertCreateSerializer,
    ExpertListSerializer,
    ExpertUpdateSerializer,
)
from authentication.services.department_service import (
    DepartmentError,
    create_department,
    get_institution_department,
    list_departments,
    rename_department,
    set_department_active,
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
                department_id=data.get('department_id'),
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
                'message': 'Expert onboarded. Login credentials have been emailed.',
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
        # Sentinel so an omitted department_id leaves it untouched (vs. an explicit
        # null which clears it). update_expert validates ownership.
        department_kwargs = {}
        if 'department_id' in data:
            department_kwargs['department_id'] = data['department_id']
        try:
            update_expert(
                profile,
                bio=data.get('bio'),
                headline=data.get('headline'),
                specialization=data.get('specialization'),
                **department_kwargs,
            )
            if 'is_active' in data:
                set_expert_active(
                    request.user.partner_institution_profile, profile, data['is_active'],
                )
        except ExpertError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
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


class InstitutionDepartmentListCreateView(APIView):
    """
    GET  /api/v1/auth/partner/departments/   → list this institution's departments.
    POST /api/v1/auth/partner/departments/   → create a department.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def get(self, request):
        institution = request.user.partner_institution_profile
        active_only = request.query_params.get('active_only', 'true').lower() != 'false'
        queryset = list_departments(institution, active_only=active_only)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = DepartmentSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response

    def post(self, request):
        institution = request.user.partner_institution_profile
        serializer = DepartmentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            department = create_department(institution, serializer.validated_data['name'])
        except DepartmentError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Department creation failed for institution %s', institution.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Department created.',
                'data': DepartmentSerializer(department).data,
            },
            status=status.HTTP_201_CREATED,
        )


class InstitutionDepartmentDetailView(APIView):
    """
    GET    /api/v1/auth/partner/departments/<id>/   → department detail.
    PATCH  /api/v1/auth/partner/departments/<id>/   → rename / toggle is_active.
    DELETE /api/v1/auth/partner/departments/<id>/   → soft-deactivate.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def _get(self, request, department_id):
        # Numeric id → 404 on no-access (never leak existence).
        return get_institution_department(request.user.partner_institution_profile, department_id)

    def get(self, request, department_id):
        try:
            department = self._get(request, department_id)
        except Department.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Department not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {'success': True, 'data': DepartmentSerializer(department).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, department_id):
        institution = request.user.partner_institution_profile
        try:
            department = self._get(request, department_id)
        except Department.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Department not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            if 'name' in request.data:
                rename_department(institution, department, request.data['name'])
            if 'is_active' in request.data:
                set_department_active(department, bool(request.data['is_active']))
        except DepartmentError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Department update failed for department %s', department.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Department updated.',
                'data': DepartmentSerializer(department).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, department_id):
        try:
            department = self._get(request, department_id)
        except Department.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Department not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Soft-deactivate: assigned experts keep their FK; it drops from the dropdown.
        set_department_active(department, False)
        return Response(
            {'success': True, 'message': 'Department deactivated.'},
            status=status.HTTP_200_OK,
        )
