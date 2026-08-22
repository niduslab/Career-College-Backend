import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.services.institution_revenue_service import (
    InstitutionRevenueError,
    build_order_queryset,
    revenue_summary,
    serialize_order_page,
)
from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsVerifiedPartnerInstitution

logger = logging.getLogger(__name__)


class InstitutionRevenueSummaryView(APIView):
    """
    GET /api/v1/analytics/partner/revenue/summary/ → gross revenue cards,
    course/webinar breakdown, and a trend series for the institution's own
    courses and webinars.

    Gross-only, deliberately: no payout/balance/commission — none of those
    have a backing model yet (Payments Phase 2).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def get(self, request):
        institution = request.user.partner_institution_profile
        try:
            data = revenue_summary(
                institution,
                granularity=request.query_params.get('granularity', 'monthly'),
                periods=request.query_params.get('periods', 6),
            )
        except Exception:
            logger.exception('Institution revenue summary failed for institution %s', institution.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'success': True, 'data': data}, status=status.HTTP_200_OK)


class InstitutionOrderListView(APIView):
    """
    GET /api/v1/analytics/partner/revenue/orders/ → paginated paid-order
    history for the institution's own courses and webinars. `?item_type=`,
    `?sort=`.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def get(self, request):
        institution = request.user.partner_institution_profile
        try:
            queryset = build_order_queryset(institution, request.query_params)
        except InstitutionRevenueError as exc:
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
            logger.exception('Institution order list failed for institution %s', institution.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response.data = {'success': True, 'data': response.data}
        return response
