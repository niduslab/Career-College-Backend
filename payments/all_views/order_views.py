import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsLearnerUser
from payments.all_models.order_models import Order
from payments.all_serializers.order_serializers import OrderSerializer
from payments.services import PaymentError, get_learner_orders

logger = logging.getLogger(__name__)


class OrderListView(APIView):
    """GET /api/v1/payments/orders/ — the caller's own payment orders.

    Optional `?status=` filter (initiated/processing/paid/failed/cancelled).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        try:
            queryset = get_learner_orders(request.user, request.query_params.get('status'))
        except PaymentError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = OrderSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class OrderDetailView(APIView):
    """GET /api/v1/payments/orders/<pk>/ — one of the caller's own orders.

    Numeric id → 404 on no-access (another learner's order is indistinguishable
    from a missing one, per the project-wide ID policy).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request, pk: int):
        order = get_object_or_404(
            Order.objects.select_related('course', 'webinar'), pk=pk, user=request.user,
        )
        return Response(
            {'success': True, 'data': OrderSerializer(order).data},
            status=status.HTTP_200_OK,
        )
