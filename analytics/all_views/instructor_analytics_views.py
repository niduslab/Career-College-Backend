import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.services.instructor_analytics_service import instructor_summary
from core.permissions import IsEmailVerified, IsInstructorUser

logger = logging.getLogger(__name__)


class InstructorAnalyticsSummaryView(APIView):
    """
    GET /api/v1/analytics/instructor/summary/ → dashboard KPI cards for one
    individual instructor's own courses.

    Gated IsInstructorUser (not IsVerifiedInstructor) — day-to-day dashboard
    viewing shouldn't require completed identity verification, matching how
    IsCourseCreator (not IsVerifiedCourseCreator) gates ordinary authoring.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def get(self, request):
        try:
            data = instructor_summary(request.user)
        except Exception:
            logger.exception('Instructor analytics summary failed for user %s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)
