import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified, IsVerifiedCourseCreator
from webinars.models import Webinar
from webinars.serializers import WebinarSerializer

logger = logging.getLogger(__name__)


def _transition_error_response(e, default_400_message):
    """Map a domain ValidationError to the project's 400/422 response shape."""
    if hasattr(e, 'message_dict'):
        return Response(
            {'success': False, 'message': default_400_message, 'errors': e.message_dict},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(
        {'success': False, 'message': e.messages[0]},
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


class WebinarPublishView(APIView):
    """
    POST {pk}/publish/ — the assigned host expert publishes the webinar directly.

    draft → published. No institution or admin approval gate. Scoped to the host
    expert (host_expert=request.user; the institution user is created_by, not the
    host → 404). Publishing runs the completeness check.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        webinar = get_object_or_404(
            Webinar.objects.filter(host_expert=request.user),
            pk=pk,
        )

        try:
            webinar.transition_to('published', actor=request.user)
        except ValidationError as e:
            return _transition_error_response(e, 'Webinar is not ready to publish.')

        _webinar_title = webinar.title
        _webinar_slug = webinar.slug
        # Notify the owning institution + the host (the publisher) that it is live.
        _recipients = [
            u for u in (
                webinar.partner_institution.user if webinar.partner_institution_id else None,
                webinar.host_expert,
            ) if u is not None
        ]

        def _notify_published():
            from notifications.models import NotificationEventType
            from notifications.services.dispatcher import dispatch
            if not _recipients:
                return
            dispatch(
                NotificationEventType.WEBINAR_PUBLISHED,
                _recipients,
                context={'webinar_title': _webinar_title, 'webinar_slug': _webinar_slug},
            )

        transaction.on_commit(_notify_published)

        return Response(
            {
                'success': True,
                'message': 'Webinar published successfully.',
                'data': WebinarSerializer(webinar).data,
            },
            status=status.HTTP_200_OK,
        )


class WebinarReworkView(APIView):
    """POST {pk}/rework/ — move an archived webinar back to draft (owner or host)."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        webinar = get_object_or_404(
            Webinar.objects.filter(
                Q(created_by=request.user) | Q(host_expert=request.user)
            ).distinct(),
            pk=pk,
        )

        try:
            webinar.transition_to('draft', actor=request.user)
        except ValidationError as e:
            return _transition_error_response(e, 'Cannot rework this webinar.')

        return Response(
            {
                'success': True,
                'message': 'Webinar moved back to draft for reworking.',
                'data': WebinarSerializer(webinar).data,
            },
            status=status.HTTP_200_OK,
        )


class WebinarArchiveView(APIView):
    """POST {pk}/archive/ — archive a published webinar (owner, host, or admin)."""

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        if request.user.is_staff or request.user.user_type == 'admin':
            webinar = get_object_or_404(Webinar, pk=pk)
        else:
            webinar = get_object_or_404(
                Webinar.objects.filter(
                    Q(created_by=request.user) | Q(host_expert=request.user)
                ).distinct(),
                pk=pk,
            )

        try:
            webinar.transition_to('archived', actor=request.user)
        except ValidationError as e:
            return _transition_error_response(e, 'Cannot archive this webinar.')

        return Response(
            {
                'success': True,
                'message': 'Webinar archived successfully.',
                'data': WebinarSerializer(webinar).data,
            },
            status=status.HTTP_200_OK,
        )
