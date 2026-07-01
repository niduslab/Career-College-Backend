from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import (
    IsEmailVerified,
    IsVerifiedCourseCreator,
    IsVerifiedPartnerInstitution,
)
from webinars.models import Webinar
from webinars.serializers import (
    WebinarCreateUpdateSerializer,
    WebinarSerializer,
)
from webinars.services import WebinarError


def _guard_editable(webinar):
    """Return a 422 Response if the webinar is locked for editing, else None."""
    if not webinar.is_editable():
        return Response(
            {
                'success': False,
                'message': (
                    f'This webinar is "{webinar.status}" and cannot be edited. '
                    'Only webinars in draft or archived status can be modified.'
                ),
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return None


class WebinarListAPIView(APIView):
    """GET list webinars where the authenticated user is the owning institution or assigned host."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]

    def get(self, request):
        queryset = (
            Webinar.objects
            .select_related('created_by', 'last_edited_by', 'category', 'partner_institution', 'host_expert')
            .prefetch_related('institutional_speakers')
            .filter(Q(created_by=request.user) | Q(host_expert=request.user))
            .distinct()
            .order_by('-created_at')
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = WebinarSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class WebinarCreateAPIView(APIView):
    """POST create a new webinar (institution-only)."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def post(self, request):
        serializer = WebinarCreateUpdateSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            webinar = serializer.save()
        except WebinarError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'Could not create the webinar.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'success': True,
                'message': 'Webinar created successfully.',
                'data': WebinarSerializer(webinar).data,
            },
            status=status.HTTP_201_CREATED,
        )


class WebinarDetailView(APIView):
    """Retrieve / partially update a webinar where user is the owning institution or assigned host."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]

    def _get_webinar(self, request, pk, owner_only=False):
        # GET is visible to the owning institution OR the assigned host; editing
        # is institution-only (owner_only), so a host expert cannot mutate
        # metadata — webinar authoring stays with the institution.
        scope = Q(created_by=request.user)
        if not owner_only:
            scope |= Q(host_expert=request.user)
        qs = (
            Webinar.objects
            .select_related('created_by', 'last_edited_by', 'category', 'partner_institution', 'host_expert')
            .prefetch_related('institutional_speakers')
            .filter(scope)
            .distinct()
        )
        return get_object_or_404(qs, pk=pk)

    def get(self, request, pk):
        webinar = self._get_webinar(request, pk)
        return Response({'success': True, 'data': WebinarSerializer(webinar).data}, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        webinar = self._get_webinar(request, pk, owner_only=True)
        if err := _guard_editable(webinar):
            return err
        serializer = WebinarCreateUpdateSerializer(
            webinar,
            data=request.data,
            partial=True,
            context={'request': request},
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            webinar = serializer.save()
        except WebinarError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'Could not update the webinar.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'success': True,
                'message': 'Webinar updated successfully.',
                'data': WebinarSerializer(webinar).data,
            },
            status=status.HTTP_200_OK,
        )
