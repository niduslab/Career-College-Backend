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

from core.permissions import IsPlatformAdmin, IsEmailVerified, IsVerifiedCourseCreator
from courses.models import NidusCourse
from courses.serializers import NidusCourseSerializer

logger = logging.getLogger(__name__)


class CourseSubmitForReviewView(APIView):

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        course = get_object_or_404(
            NidusCourse.objects.filter(
                Q(instructors=request.user) | Q(created_by=request.user)
            ).distinct(),
            pk=pk,
        )

        try:
            course.transition_to('under_review')
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Course is not ready for submission.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        _course_id = course.pk
        _course_title = course.title
        _course_slug = course.slug
        _instructor_name = request.user.get_full_name() or request.user.email

        def _notify_submitted():
            from authentication.models import User
            from notifications.models import NotificationEventType
            from notifications.services.dispatcher import dispatch
            admins = list(User.objects.filter(user_type='admin', is_deleted=False, is_active=True))
            if admins:
                dispatch(
                    NotificationEventType.COURSE_SUBMITTED,
                    admins,
                    context={
                        'course_id': _course_id,
                        'course_title': _course_title,
                        'course_slug': _course_slug,
                        'instructor_name': _instructor_name,
                    },
                )

        transaction.on_commit(_notify_submitted)

        return Response(
            {
                'success': True,
                'message': 'Course submitted for review.',
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_200_OK,
        )


class CourseAdminReviewView(APIView):

    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        course = get_object_or_404(NidusCourse, pk=pk)

        action = request.data.get('action', '').strip().lower()
        if action not in ('approve', 'reject'):
            return Response(
                {'success': False, 'message': 'action must be "approve" or "reject".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_status = 'published' if action == 'approve' else 'rejected'
        rejection_reason = request.data.get('rejection_reason', '')

        try:
            course.transition_to(
                new_status,
                reviewer=request.user,
                rejection_reason=rejection_reason,
            )
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Review action failed.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        _course_title = course.title
        _course_slug = course.slug
        _rejection_reason = rejection_reason
        _action = action
        _instructors_snapshot = list(course.instructors.all())

        def _notify_review_decision():
            from notifications.models import NotificationEventType
            from notifications.services.dispatcher import dispatch
            if _action == 'approve':
                dispatch(
                    NotificationEventType.COURSE_APPROVED,
                    _instructors_snapshot,
                    context={'course_title': _course_title, 'course_slug': _course_slug},
                )
            else:
                dispatch(
                    NotificationEventType.COURSE_REJECTED,
                    _instructors_snapshot,
                    context={
                        'course_title': _course_title,
                        'course_slug': _course_slug,
                        'rejection_reason': _rejection_reason,
                    },
                )

        transaction.on_commit(_notify_review_decision)

        message = (
            'Course approved successfully.'
            if action == 'approve'
            else 'Course rejected successfully.'
        )
        return Response(
            {
                'success': True,
                'message': message,
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_200_OK,
        )


class CourseReworkView(APIView):

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        course = get_object_or_404(
            NidusCourse.objects.filter(
                Q(instructors=request.user) | Q(created_by=request.user)
            ).distinct(),
            pk=pk,
        )

        try:
            course.transition_to('draft')
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Cannot rework this course.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            {
                'success': True,
                'message': 'Course moved back to draft for reworking.',
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_200_OK,
        )


class CourseArchiveView(APIView):

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        if request.user.is_staff or request.user.user_type == 'admin':
            course = get_object_or_404(NidusCourse, pk=pk)
        else:
            course = get_object_or_404(
                NidusCourse.objects.filter(
                    Q(instructors=request.user) | Q(created_by=request.user)
                ).distinct(),
                pk=pk,
            )

        try:
            course.transition_to('archived')
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Cannot archive this course.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            {
                'success': True,
                'message': 'Course archived successfully.',
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_200_OK,
        )


class CourseRestoreView(APIView):

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        if request.user.is_staff or request.user.user_type == 'admin':
            course = get_object_or_404(NidusCourse, pk=pk)
        else:
            course = get_object_or_404(
                NidusCourse.objects.filter(
                    Q(instructors=request.user) | Q(created_by=request.user)
                ).distinct(),
                pk=pk,
            )

        try:
            course.transition_to('draft')
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Cannot restore this course.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(
            {
                'success': True,
                'message': 'Course restored to draft.',
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_200_OK,
        )
