import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.services.admin_analytics_service import (
    certificate_trend,
    conversion_funnel,
    enrollment_trend,
    platform_summary,
    revenue_trend,
    top_courses,
    user_signup_trend,
)
from core.permissions import IsEmailVerified, IsPlatformAdmin

logger = logging.getLogger(__name__)

_ADMIN_PERMS = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]
_ERROR_BODY = {'success': False, 'message': 'An unexpected error occurred. Please try again.'}


class AdminAnalyticsSummaryView(APIView):
    """GET /api/v1/analytics/admin/summary/ → platform KPI cards."""

    permission_classes = _ADMIN_PERMS

    def get(self, request):
        try:
            data = platform_summary()
        except Exception:
            logger.exception('Admin analytics summary failed')
            return Response(_ERROR_BODY, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)


class _AdminBaseTrendView(APIView):
    """Shared admin trend plumbing: raw param read + envelope + error handling."""

    permission_classes = _ADMIN_PERMS
    trend_label = 'trend'

    def _series(self, granularity, periods):
        raise NotImplementedError

    def get(self, request):
        granularity = request.query_params.get('granularity', 'monthly')
        periods = request.query_params.get('periods')
        try:
            granularity, periods, series = self._series(granularity, periods)
        except Exception:
            logger.exception('Admin analytics %s trend failed', self.trend_label)
            return Response(_ERROR_BODY, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(
            {'success': True, 'data': {'granularity': granularity, 'periods': periods, 'series': series}},
            status=status.HTTP_200_OK,
        )


class AdminUserTrendView(_AdminBaseTrendView):
    """GET /api/v1/analytics/admin/users/trend/ → new-signup series."""

    trend_label = 'user-signup'

    def _series(self, granularity, periods):
        return user_signup_trend(granularity, periods)


class AdminEnrollmentTrendView(_AdminBaseTrendView):
    """GET /api/v1/analytics/admin/enrollments/trend/"""

    trend_label = 'enrollment'

    def _series(self, granularity, periods):
        return enrollment_trend(granularity, periods)


class AdminCertificateTrendView(_AdminBaseTrendView):
    """GET /api/v1/analytics/admin/certificates/trend/"""

    trend_label = 'certificate'

    def _series(self, granularity, periods):
        return certificate_trend(granularity, periods)


class AdminRevenueTrendView(_AdminBaseTrendView):
    """GET /api/v1/analytics/admin/revenue/trend/ → paid-order gross per bucket."""

    trend_label = 'revenue'

    def _series(self, granularity, periods):
        return revenue_trend(granularity, periods)


class AdminTopCoursesView(APIView):
    """GET /api/v1/analytics/admin/top-courses/ → ranked platform courses."""

    permission_classes = _ADMIN_PERMS

    def get(self, request):
        sort = request.query_params.get('sort', 'enrollments')
        limit = request.query_params.get('limit')
        try:
            data = top_courses(sort, limit)
        except Exception:
            logger.exception('Admin analytics top-courses failed')
            return Response(_ERROR_BODY, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)


class AdminFunnelView(APIView):
    """GET /api/v1/analytics/admin/funnel/ → signup→enroll→complete→certify."""

    permission_classes = _ADMIN_PERMS

    def get(self, request):
        try:
            data = conversion_funnel()
        except Exception:
            logger.exception('Admin analytics funnel failed')
            return Response(_ERROR_BODY, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)
