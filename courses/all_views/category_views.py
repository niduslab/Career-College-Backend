import logging

from django.db import IntegrityError
from django.db.models import Prefetch
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsPlatformAdmin
from courses.models import CourseCategory
from courses.serializers import (
    CourseCategoryTreeSerializer,
    CourseCategoryWriteSerializer,
)

logger = logging.getLogger(__name__)


class CourseCategoryListCreateView(APIView):
    """Public: list active categories as a nested tree. Admin: create a category."""

    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), IsEmailVerified(), IsPlatformAdmin()]
        return [AllowAny()]

    def get(self, request):
        queryset = (
            CourseCategory.objects
            .filter(is_active=True, parent__isnull=True)
            .prefetch_related(
                Prefetch('children', queryset=CourseCategory.objects.filter(is_active=True).order_by('name'))
            )
            .order_by('name')
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = CourseCategoryTreeSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response

    def post(self, request):
        serializer = CourseCategoryWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.save()
        except IntegrityError as e:
            logger.error(f"Category create failed for user {request.user.id}: {e}")
            return Response(
                {'success': False, 'message': 'A category with this name or slug already exists.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            {'success': True, 'message': 'Category created.', 'data': serializer.data},
            status=status.HTTP_201_CREATED,
        )


class CourseCategoryDetailView(APIView):
    """Admin-only: retrieve, update, or soft-deactivate a single category."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_object(self, pk):
        return CourseCategory.objects.get(pk=pk)

    def get(self, request, pk):
        try:
            category = self._get_object(pk)
        except CourseCategory.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Category not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CourseCategoryWriteSerializer(category)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        try:
            category = self._get_object(pk)
        except CourseCategory.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Category not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CourseCategoryWriteSerializer(category, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.save()
        except IntegrityError as e:
            logger.error(f"Category update failed for category {pk}: {e}")
            return Response(
                {'success': False, 'message': 'A category with this name or slug already exists.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            {'success': True, 'message': 'Category updated.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        try:
            category = self._get_object(pk)
        except CourseCategory.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Category not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        category.is_active = False
        category.save(update_fields=['is_active', 'updated_at'])
        return Response(
            {'success': True, 'message': 'Category deactivated.'},
            status=status.HTTP_200_OK,
        )
