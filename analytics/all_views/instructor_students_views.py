import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.services.instructor_students_service import (
    InstructorStudentsError,
    build_student_queryset,
    instructor_course_options,
    serialize_student_page,
    students_summary,
)
from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsInstructorUser

logger = logging.getLogger(__name__)


class InstructorStudentListView(APIView):
    """
    GET /api/v1/analytics/instructor/students/ → paginated roster of learners
    enrolled in the caller's own courses. One row per enrollment, not per
    learner — see docs/architecture/30-instructor-students.md.

    Gated IsInstructorUser (not IsVerifiedInstructor), matching the analytics
    summary: viewing your own roster shouldn't require completed identity
    verification.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def get(self, request):
        try:
            queryset = build_student_queryset(request.user, request.query_params)
        except InstructorStudentsError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )

        try:
            paginator = StandardResultsSetPagination()
            page = paginator.paginate_queryset(queryset, request)
            rows = serialize_student_page(page)
            response = paginator.get_paginated_response(rows)
        except Exception:
            logger.exception('Instructor student list failed for user %s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response.data = {'success': True, 'data': response.data}
        return response


class InstructorStudentSummaryView(APIView):
    """
    GET /api/v1/analytics/instructor/students/summary/ → roster-wide KPIs and
    the course-filter options.

    Separate from the list because these describe every student, not the
    current page — the same reason My Courses returns server-side
    `status_counts` instead of letting the client count rows.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def get(self, request):
        try:
            data = students_summary(request.user)
            data['courses'] = instructor_course_options(request.user)
        except Exception:
            logger.exception('Instructor student summary failed for user %s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)
