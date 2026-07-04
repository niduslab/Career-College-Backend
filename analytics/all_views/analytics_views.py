import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.services import (
    ATTRIBUTION_NOTE,
    certificate_trend,
    enrollment_trend,
    expert_performance,
    institution_summary,
    top_courses,
    webinar_registration_trend,
)
from authentication.models import InstructorProfile
from core.permissions import IsEmailVerified, IsVerifiedPartnerInstitution

logger = logging.getLogger(__name__)


class InstitutionAnalyticsSummaryView(APIView):
    """GET /api/v1/analytics/partner/summary/ → dashboard KPI cards."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def get(self, request):
        institution = request.user.partner_institution_profile
        try:
            data = institution_summary(institution)
        except Exception:
            logger.exception('Analytics summary failed for institution %s', institution.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)


class _BaseTrendView(APIView):
    """Shared trend-endpoint plumbing: param parsing + envelope + error handling."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]
    trend_label = 'trend'

    def _series(self, institution, granularity, periods):
        raise NotImplementedError

    def get(self, request):
        institution = request.user.partner_institution_profile
        granularity = request.query_params.get('granularity', 'monthly')
        periods = request.query_params.get('periods')
        try:
            granularity, periods, series = self._series(institution, granularity, periods)
        except Exception:
            logger.exception('Analytics %s trend failed for institution %s', self.trend_label, institution.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'data': {'granularity': granularity, 'periods': periods, 'series': series}},
            status=status.HTTP_200_OK,
        )


class InstitutionEnrollmentTrendView(_BaseTrendView):
    """GET /api/v1/analytics/partner/enrollments/trend/"""

    trend_label = 'enrollment'

    def _series(self, institution, granularity, periods):
        return enrollment_trend(institution, granularity, periods)


class InstitutionWebinarTrendView(_BaseTrendView):
    """GET /api/v1/analytics/partner/webinars/trend/"""

    trend_label = 'webinar'

    def _series(self, institution, granularity, periods):
        return webinar_registration_trend(institution, granularity, periods)


class InstitutionCertificateTrendView(_BaseTrendView):
    """GET /api/v1/analytics/partner/certificates/trend/"""

    trend_label = 'certificate'

    def _series(self, institution, granularity, periods):
        return certificate_trend(institution, granularity, periods)


class InstitutionTopCoursesView(APIView):
    """GET /api/v1/analytics/partner/top-courses/ → ranked courses."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def get(self, request):
        institution = request.user.partner_institution_profile
        sort = request.query_params.get('sort', 'enrollments')
        limit = request.query_params.get('limit')
        try:
            data = top_courses(institution, sort, limit)
        except Exception:
            logger.exception('Analytics top-courses failed for institution %s', institution.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)


class InstitutionExpertPerformanceView(APIView):
    """GET /api/v1/analytics/partner/experts/performance/ → per-expert metrics."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def get(self, request):
        institution = request.user.partner_institution_profile
        try:
            experts = expert_performance(institution)
        except Exception:
            logger.exception('Expert performance failed for institution %s', institution.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'data': {'attribution': ATTRIBUTION_NOTE, 'experts': experts}},
            status=status.HTTP_200_OK,
        )


class InstitutionExpertPerformanceDetailView(APIView):
    """GET /api/v1/analytics/partner/experts/<expert_id>/performance/ → one expert."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def get(self, request, expert_id: int):
        institution = request.user.partner_institution_profile
        try:
            row = expert_performance(institution, expert_id=expert_id)
        except InstructorProfile.DoesNotExist:
            # Numeric id → 404 on no-access (never leak another institution's expert).
            return Response(
                {'success': False, 'message': 'Expert not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception:
            logger.exception(
                'Expert performance detail failed for institution %s expert %s',
                institution.pk, expert_id,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'data': {'attribution': ATTRIBUTION_NOTE, 'expert': row}},
            status=status.HTTP_200_OK,
        )
