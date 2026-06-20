import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified, IsVerifiedPartnerInstitution
from courses.models import NidusCourse
from courses.services.institution_course_service import (
    InstitutionCourseError,
    add_course_instructor,
    remove_course_instructor,
)

logger = logging.getLogger(__name__)


class InstitutionCourseInstructorView(APIView):
    """
    POST   /courses/<pk>/institution-instructors/          → add an expert to the roster.
    DELETE /courses/<pk>/institution-instructors/<uid>/    → remove an expert.

    Only the owning verified partner institution may manage the roster. Numeric
    course id → 404 on no-access (never leak existence).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def _get_owned_course(self, request, pk):
        institution = request.user.partner_institution_profile
        try:
            return NidusCourse.objects.get(pk=pk, partner_institution=institution), institution
        except NidusCourse.DoesNotExist:
            return None, institution

    def post(self, request, pk):
        course, institution = self._get_owned_course(request, pk)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        expert_user_id = request.data.get('expert_user_id')
        if expert_user_id in (None, ''):
            return Response(
                {'success': False, 'message': 'Validation failed.',
                 'errors': {'expert_user_id': 'This field is required.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            expert_user_id = int(expert_user_id)
        except (TypeError, ValueError):
            return Response(
                {'success': False, 'message': 'Validation failed.',
                 'errors': {'expert_user_id': 'Must be a valid user id.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            add_course_instructor(course, institution, expert_user_id)
        except InstitutionCourseError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Add course instructor failed: course=%s', pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Expert assigned to course.'},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk, expert_user_id):
        course, institution = self._get_owned_course(request, pk)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            remove_course_instructor(course, institution, expert_user_id)
        except InstitutionCourseError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Remove course instructor failed: course=%s', pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Expert removed from course.'},
            status=status.HTTP_200_OK,
        )
