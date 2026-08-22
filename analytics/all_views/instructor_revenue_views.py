import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.services.instructor_revenue_service import (
    InstructorRevenueError,
    build_order_queryset,
    revenue_summary,
    serialize_order_page,
)
from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsInstructorUser

logger = logging.getLogger(__name__)


class InstructorRevenueSummaryView(APIView):
    """
    GET /api/v1/analytics/instructor/revenue/summary/ → gross revenue cards,
    per-course breakdown, and a trend series for the caller's own courses.

    Gross-only, deliberately: docs/architecture/31-instructor-revenue.md.
    No payout/balance/commission — none of those have a backing model yet.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def get(self, request):
        try:
            data = revenue_summary(
                request.user,
                granularity=request.query_params.get('granularity', 'monthly'),
                periods=request.query_params.get('periods', 6),
            )
        except Exception:
            logger.exception('Instructor revenue summary failed for user %s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)


class InstructorOrderListView(APIView):
    """
    GET /api/v1/analytics/instructor/revenue/orders/ → paginated paid-order
    history for the caller's own courses. `?course_id=`, `?sort=`.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def get(self, request):
        try:
            queryset = build_order_queryset(request.user, request.query_params)
        except InstructorRevenueError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )

        try:
            paginator = StandardResultsSetPagination()
            page = paginator.paginate_queryset(queryset, request)
            rows = serialize_order_page(page)
            response = paginator.get_paginated_response(rows)
        except Exception:
            logger.exception('Instructor order list failed for user %s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response.data = {'success': True, 'data': response.data}
        return response
