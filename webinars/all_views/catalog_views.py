import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from webinars.models import Webinar
from webinars.serializers import (
    CatalogWebinarDetailSerializer,
    CatalogWebinarListSerializer,
)
from webinars.services import filter_catalog_webinars, get_catalog_webinars

logger = logging.getLogger(__name__)


class CatalogWebinarListView(APIView):
    """GET /api/v1/webinars/catalog/ — public list of published webinars, soonest first."""

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            queryset = filter_catalog_webinars(get_catalog_webinars(), request.query_params)
        except ValidationError as e:
            return Response(
                {'success': False, 'message': 'Invalid filter parameters.', 'errors': e.message_dict},
                status=status.HTTP_400_BAD_REQUEST,
            )

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = CatalogWebinarListSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class CatalogWebinarDetailView(APIView):
    """GET /api/v1/webinars/catalog/{slug}/ — public detail (no meeting_url)."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        webinar = get_object_or_404(
            Webinar.objects
            .select_related('created_by', 'category', 'partner_institution', 'host_expert')
            .prefetch_related('institutional_speakers'),
            slug=slug,
            is_published=True,
        )
        return Response(
            {'success': True, 'data': CatalogWebinarDetailSerializer(webinar).data},
            status=status.HTTP_200_OK,
        )
