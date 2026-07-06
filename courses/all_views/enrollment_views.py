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
    EnrollmentSerializer,
    MyCourseDetailSerializer,
)
from courses.services import (
    enroll_learner,
    filter_catalog_courses,
    get_catalog_courses,
    get_learner_enrollments,
    load_catalog_curriculum,
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

    Public paginated list of published courses. Multi-criteria filtering
    and sorting are delegated to ``filter_catalog_courses`` — see that
    function's docstring for the full query-param contract.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            queryset = filter_catalog_courses(get_catalog_courses(), request.query_params)
        except ValidationError as e:
            return Response(
                {'success': False, 'message': 'Invalid filter parameters.', 'errors': e.message_dict},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            NidusCourse.objects.select_related('created_by', 'category', 'partner_institution').prefetch_related(
                'instructors',
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

        enrollment_type = Enrollment.EnrollmentType.FREE
        if course.price > 0:
            # Paid course: direct enrollment only for learners who already
            # purchased (covers unenroll → re-enroll without a second charge).
            # Local import — keeps courses→payments off the module-load path.
            from payments.all_models.order_models import Order
            has_paid = Order.objects.filter(
                user=request.user, course=course, status=Order.Status.PAID,
            ).exists()
            if not has_paid:
                return Response(
                    {
                        'success': False,
                        'message': 'This is a paid course. Complete payment via the checkout endpoint to enroll.',
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            enrollment_type = Enrollment.EnrollmentType.PAID

        try:
            enrollment = enroll_learner(request.user, course, enrollment_type=enrollment_type)
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

    Slim course-header payload for the learner's course page: metadata
    (title, description, instructors, objectives, totals) + enrollment
    status (overall progress, completed_at, last_accessed_at). The
    frontend pairs this with `/learn/<slug>/curriculum/` for the sidebar
    tree and per-item `/learn/<thing>/<id>/` endpoints for playable
    content.

    Accessible to:
    - learners with an active enrollment for this course
    - the course's own instructors (preview, no enrollment row needed)
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser | IsInstructorUser]

    def get(self, request, slug):
        course = get_object_or_404(
            NidusCourse.objects.select_related('created_by', 'category', 'partner_institution').prefetch_related(
                'instructors',
                'learning_objectives',
                'prerequisites',
                'audiences',
            ),
            slug=slug,
        )

        # Use the prefetched `instructors` cache (loaded above) — `.filter().exists()`
        # would bypass the prefetch and issue a redundant SQL query.
        is_instructor = any(u.pk == request.user.pk for u in course.instructors.all())
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

        serializer = MyCourseDetailSerializer(
            course,
            context={'is_instructor': is_instructor, 'enrollment': enrollment},
        )
        return Response(
            {'success': True, 'data': serializer.data},
            status=status.HTTP_200_OK,
        )
