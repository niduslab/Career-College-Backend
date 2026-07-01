import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsLearnerUser
from webinars.models import Webinar, WebinarRegistration
from webinars.serializers import WebinarRegistrationSerializer
from webinars.services import WebinarError, register_for_webinar

logger = logging.getLogger(__name__)


class WebinarRegisterView(APIView):
    """
    POST /api/v1/webinars/{slug}/register/

    Register the authenticated learner for a published webinar.
    Slug-based → 403 (course slugs are public; existence is not leaked by a 404).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, slug):
        webinar = get_object_or_404(Webinar, slug=slug, is_published=True)

        try:
            registration = register_for_webinar(request.user, webinar)
        except WebinarError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )

        return Response(
            {
                'success': True,
                'message': 'Registered successfully.',
                'data': WebinarRegistrationSerializer(registration).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MyWebinarsListView(APIView):
    """GET /api/v1/webinars/my-webinars/ — the learner's active registrations."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        queryset = (
            WebinarRegistration.objects
            .filter(user=request.user, is_active=True)
            .select_related(
                'webinar', 'webinar__partner_institution',
                'webinar__category', 'webinar__host_expert',
            )
            .order_by('-created_at')
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = WebinarRegistrationSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class MyWebinarDetailView(APIView):
    """
    GET /api/v1/webinars/my-webinars/{slug}/

    Registrant-facing detail — exposes meeting_url. Slug-based → 403 when the
    caller is not registered.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request, slug):
        webinar = get_object_or_404(Webinar, slug=slug)

        registration = (
            WebinarRegistration.objects
            .select_related(
                'webinar', 'webinar__partner_institution',
                'webinar__category', 'webinar__host_expert',
            )
            .filter(user=request.user, webinar=webinar, is_active=True)
            .first()
        )
        if registration is None:
            return Response(
                {'success': False, 'message': 'You are not registered for this webinar.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            {'success': True, 'data': WebinarRegistrationSerializer(registration).data},
            status=status.HTTP_200_OK,
        )
