import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsInstructorUser, IsLearnerUser
from courses.models import Enrollment, NidusCourse
from courses.serializers import (
    CatalogCourseDetailSerializer,
    CatalogCourseListSerializer,
    EnrolledCourseContentSerializer,
    EnrollmentSerializer,
)
from courses.services import (
    enroll_learner,
    get_catalog_courses,
    get_learner_enrollments,
    load_catalog_curriculum,
    load_consumption_curriculum,
    unenroll_learner,
    update_last_accessed,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Public catalog (no auth required)
# =============================================================================

class CatalogCourseListView(APIView):
    """
    GET /api/v1/courses/catalog/

    Public paginated list of published courses. Supports filtering
    via query params: ?category=<slug>&level=<level>&language=<lang>&search=<text>
    """

    permission_classes = [AllowAny]

    def get(self, request):
        queryset = get_catalog_courses()

        # ── Optional filters ──
        category_slug = request.query_params.get('category')
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)

        level = request.query_params.get('level')
        if level:
            queryset = queryset.filter(level=level)

        language = request.query_params.get('language')
        if language:
            queryset = queryset.filter(language__iexact=language)

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(title__icontains=search)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = CatalogCourseListSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class CatalogCourseDetailView(APIView):
    """
    GET /api/v1/courses/catalog/{slug}/

    Public detail view of a published course. Returns metadata + a curriculum
    outline (section titles, item titles, lecture durations, and is_preview
    flag). Preview lecture playlist URLs are included only for lectures
    explicitly marked as preview.
    """

    permission_classes = [AllowAny]

    def get(self, request, slug):
        course = get_object_or_404(
            NidusCourse.objects.select_related('created_by', 'category').prefetch_related(
                'instructors',
                'partner_institutions',
                'learning_objectives',
                'prerequisites',
                'audiences',
            ),
            slug=slug,
            is_published=True,
        )
        context = load_catalog_curriculum(course)
        return Response(
            {'success': True, 'data': CatalogCourseDetailSerializer(course, context=context).data},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Enrollment actions (authenticated learner)
# =============================================================================

class CourseEnrollView(APIView):
    """
    POST /api/v1/courses/{slug}/enroll/

    Enroll the authenticated learner in a published course.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, slug):
        course = get_object_or_404(NidusCourse, slug=slug, is_published=True)

        try:
            enrollment = enroll_learner(request.user, course)
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Enrollment failed.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            {
                'success': True,
                'message': 'Enrolled successfully.',
                'data': EnrollmentSerializer(enrollment).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CourseUnenrollView(APIView):
    """
    POST /api/v1/courses/{slug}/unenroll/

    Soft-deactivate the learner's enrollment. Progress is preserved.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, slug):
        course = get_object_or_404(NidusCourse, slug=slug)

        try:
            enrollment = unenroll_learner(request.user, course)
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Unenrollment failed.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            {
                'success': True,
                'message': 'Unenrolled successfully. Your progress has been preserved.',
                'data': EnrollmentSerializer(enrollment).data,
            },
            status=status.HTTP_200_OK,
        )


# =============================================================================
# My Courses (learner dashboard)
# =============================================================================

class MyCoursesListView(APIView):
    """
    GET /api/v1/courses/my-courses/

    Paginated list of the authenticated learner's active enrollments.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        queryset = get_learner_enrollments(request.user)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = EnrollmentSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class MyCoursesDetailView(APIView):
    """
    GET /api/v1/courses/my-courses/{slug}/

    Full course-consumption payload: course metadata + curriculum tree with
    playable lecture URLs, quiz questions, coding exercises, and assignments.

    Accessible to:
    - learners with an active enrollment for this course
    - the course's own instructors (so they can preview as a learner sees it)

    Sensitive instructor-only fields (solution_code, hidden test cases,
    model_answer, quiz answer correctness) are stripped for learner callers.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser | IsInstructorUser]

    def get(self, request, slug):
        course = get_object_or_404(
            NidusCourse.objects.select_related('created_by', 'category').prefetch_related(
                'instructors',
                'partner_institutions',
                'learning_objectives',
                'prerequisites',
                'audiences',
            ),
            slug=slug,
        )

        is_instructor = course.instructors.filter(pk=request.user.pk).exists()
        enrollment = None
        if not is_instructor:
            enrollment = Enrollment.objects.filter(
                user=request.user, course=course, is_active=True,
            ).first()
            if enrollment is None:
                return Response(
                    {'success': False, 'message': 'You do not have access to this course.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            update_last_accessed(enrollment)

        context = load_consumption_curriculum(course, request.user, is_instructor)
        context['enrollment'] = enrollment

        return Response(
            {'success': True, 'data': EnrolledCourseContentSerializer(course, context=context).data},
            status=status.HTTP_200_OK,
        )
