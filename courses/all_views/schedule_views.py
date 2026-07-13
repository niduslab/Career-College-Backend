import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsVerifiedCourseCreator
from courses.all_serializers.schedule_serializers import (
    CourseScheduleCreateUpdateSerializer,
    CourseScheduleSerializer,
)
from courses.services.schedule_service import (
    ScheduleError,
    activate_schedule,
    archive_schedule,
    assert_course_supports_schedules,
    delete_schedule,
    get_course_for_schedule_manage,
    get_course_for_schedule_read,
    get_course_schedules,
    get_schedule,
    rework_schedule,
)
from courses.utils import save_authored

logger = logging.getLogger(__name__)

_SCHEDULE_PERMISSIONS = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]


def _schedule_error_response(exc):
    return Response(
        {'success': False, 'message': exc.message},
        status=exc.http_status,
    )


def _validation_error_response(exc):
    """Domain ValidationError: dict → 400 with errors, plain string → 422."""
    if hasattr(exc, 'message_dict'):
        return Response(
            {'success': False, 'message': 'Action failed.', 'errors': exc.message_dict},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(
        {'success': False, 'message': exc.messages[0]},
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


class CourseScheduleListCreateView(APIView):
    """List (read scope) / create (manage scope) schedules of a course."""

    permission_classes = _SCHEDULE_PERMISSIONS

    def get(self, request, pk):
        try:
            course = get_course_for_schedule_read(request.user, pk)
        except ScheduleError as exc:
            return _schedule_error_response(exc)

        queryset = get_course_schedules(course)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = CourseScheduleSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response

    def post(self, request, pk):
        try:
            course = get_course_for_schedule_manage(request.user, pk)
            assert_course_supports_schedules(course)
        except ScheduleError as exc:
            return _schedule_error_response(exc)

        serializer = CourseScheduleCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            schedule = save_authored(serializer, request.user, course=course)
        except Exception as e:
            logger.error(f"Schedule creation failed for course {course.pk}: {e}")
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Schedule created.',
                'data': CourseScheduleSerializer(schedule).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CourseScheduleDetailView(APIView):
    """Retrieve / patch / delete one schedule of a course."""

    permission_classes = _SCHEDULE_PERMISSIONS

    def get(self, request, pk, schedule_id):
        try:
            course = get_course_for_schedule_read(request.user, pk)
            schedule = get_schedule(course, schedule_id)
        except ScheduleError as exc:
            return _schedule_error_response(exc)

        return Response(
            {'success': True, 'data': CourseScheduleSerializer(schedule).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, pk, schedule_id):
        try:
            course = get_course_for_schedule_manage(request.user, pk)
            schedule = get_schedule(course, schedule_id)
        except ScheduleError as exc:
            return _schedule_error_response(exc)

        if not schedule.is_editable():
            return Response(
                {
                    'success': False,
                    'message': (
                        f'This schedule is "{schedule.status}" and cannot be edited. '
                        'Only draft or scheduled cohorts can be modified.'
                    ),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        serializer = CourseScheduleCreateUpdateSerializer(schedule, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            schedule = save_authored(serializer, request.user)
        except Exception as e:
            logger.error(f"Schedule update failed for schedule {schedule_id}: {e}")
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Schedule updated.',
                'data': CourseScheduleSerializer(schedule).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk, schedule_id):
        try:
            course = get_course_for_schedule_manage(request.user, pk)
            schedule = get_schedule(course, schedule_id)
            delete_schedule(schedule)
        except ScheduleError as exc:
            return _schedule_error_response(exc)

        return Response(
            {'success': True, 'message': 'Schedule deleted.'},
            status=status.HTTP_200_OK,
        )


class _CourseScheduleTransitionView(APIView):
    """Shared POST handler for schedule status transitions (manage scope)."""

    permission_classes = _SCHEDULE_PERMISSIONS
    transition = None          # callable(schedule, actor)
    success_message = ''

    def post(self, request, pk, schedule_id):
        try:
            course = get_course_for_schedule_manage(request.user, pk)
            schedule = get_schedule(course, schedule_id)
        except ScheduleError as exc:
            return _schedule_error_response(exc)

        try:
            schedule = type(self).transition(schedule, request.user)
        except ValidationError as e:
            return _validation_error_response(e)

        return Response(
            {
                'success': True,
                'message': self.success_message,
                'data': CourseScheduleSerializer(schedule).data,
            },
            status=status.HTTP_200_OK,
        )


class CourseScheduleActivateView(_CourseScheduleTransitionView):
    """POST draft → scheduled (runs the activation completeness check)."""

    transition = staticmethod(activate_schedule)
    success_message = 'Schedule activated.'


class CourseScheduleArchiveView(_CourseScheduleTransitionView):
    """POST completed → archived."""

    transition = staticmethod(archive_schedule)
    success_message = 'Schedule archived.'


class CourseScheduleReworkView(_CourseScheduleTransitionView):
    """POST archived → draft, or scheduled → draft (pull back a premature activation)."""

    transition = staticmethod(rework_schedule)
    success_message = 'Schedule moved back to draft.'
