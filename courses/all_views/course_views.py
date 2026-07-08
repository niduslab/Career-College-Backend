from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsVerifiedCourseCreator
from courses.models import NidusCourse
from courses.serializers import (
    NidusCourseCreateUpdateSerializer,
    NidusCourseSerializer,
)
from courses.utils import guard_editable, owned_course_qs


class CourseListAPIView(APIView):
    """GET list courses where authenticated user is owner or assigned instructor."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]

    def get(self, request):
        queryset = (
            owned_course_qs(request.user)
            .select_related('created_by', 'category', 'partner_institution')
            .prefetch_related('instructors')
            .order_by('-created_at')
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = NidusCourseSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class CourseCreateAPIView(APIView):
    """POST create a new Nidus course."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]

    def post(self, request):
        serializer = NidusCourseCreateUpdateSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            course = serializer.save()
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'Course contains duplicate related items.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'success': True,
                'message': 'Course created successfully.',
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CourseDetailView(APIView):
    """Retrieve and partially update a course where user is owner or assigned instructor."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]

    def _get_course(self, request, pk):
        qs = (
            owned_course_qs(request.user)
            .select_related('category', 'partner_institution')
            .prefetch_related('instructors')
        )
        return get_object_or_404(qs, pk=pk)

    def get(self, request, pk):
        course = self._get_course(request, pk)
        return Response({'success': True, 'data': NidusCourseSerializer(course).data}, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        course = self._get_course(request, pk)
        if err := guard_editable(course):
            return err
        serializer = NidusCourseCreateUpdateSerializer(
            course,
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
            course = serializer.save()
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'Course contains duplicate related items.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'success': True,
                'message': 'Course updated successfully.',
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_200_OK,
        )
