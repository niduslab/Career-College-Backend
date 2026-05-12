import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsPlatformAdmin, IsEmailVerified, IsVerifiedInstructor
from courses.models import NidusCourse
from courses.serializers import NidusCourseSerializer

logger = logging.getLogger(__name__)


class CourseSubmitForReviewView(APIView):
    """
    POST /api/v1/courses/{pk}/submit/

    Instructor submits a draft course for admin review.
    Validates course completeness before allowing the transition.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        course = get_object_or_404(NidusCourse, pk=pk, instructors=request.user)

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

        return Response(
            {
                'success': True,
                'message': 'Course submitted for review.',
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_200_OK,
        )


class CourseAdminReviewView(APIView):
    """
    POST /api/v1/courses/{pk}/review/

    Admin approves or rejects a submitted course.
    Body: {"action": "approve"} or {"action": "reject", "rejection_reason": "..."}
    """

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
    """
    POST /api/v1/courses/{pk}/rework/

    Instructor moves a rejected course back to draft for reworking.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        course = get_object_or_404(NidusCourse, pk=pk, instructors=request.user)

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
    """
    POST /api/v1/courses/{pk}/archive/

    Instructor or admin archives a published course.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        if request.user.is_staff or request.user.user_type == 'admin':
            course = get_object_or_404(NidusCourse, pk=pk)
        else:
            course = get_object_or_404(NidusCourse, pk=pk, instructors=request.user)

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
    """
    POST /api/v1/courses/{pk}/restore/

    Instructor or admin restores an archived course back to draft.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        if request.user.is_staff or request.user.user_type == 'admin':
            course = get_object_or_404(NidusCourse, pk=pk)
        else:
            course = get_object_or_404(NidusCourse, pk=pk, instructors=request.user)

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
