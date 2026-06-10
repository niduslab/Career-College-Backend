import logging

from django.db import IntegrityError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsInstructorUser, IsVerifiedCourseCreator
from courses.all_serializers.invite_serializers import (
    CourseInstructorInviteCreateSerializer,
    CourseInstructorInviteOwnerSerializer,
    CourseInstructorInviteSerializer,
)
from courses.models import CourseInstructorInvite, NidusCourse
from courses.services.invite_service import (
    InviteError,
    accept_instructor_invite,
    create_instructor_invite,
    decline_instructor_invite,
    revoke_instructor_invite,
)
from courses.utils import guard_editable

logger = logging.getLogger(__name__)


class CourseInstructorInviteCreateView(APIView):
    """
    POST /courses/<pk>/instructors/invite/
    Owner sends a co-instructor invite by email.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]

    def post(self, request, pk):
        # Numeric-ID endpoint: non-owners get 404 (per 403/404 policy).
        try:
            course = NidusCourse.objects.get(pk=pk, created_by=request.user)
        except NidusCourse.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if err := guard_editable(course):
            return err

        serializer = CourseInstructorInviteCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            invite = create_instructor_invite(
                course=course,
                owner=request.user,
                email=serializer.validated_data['email'],
            )
        except InviteError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A pending invite already exists for this user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.error('CourseInstructorInviteCreateView: unexpected error: %s', exc, exc_info=True)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Invite sent successfully.',
                'data': CourseInstructorInviteOwnerSerializer(invite).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CourseInstructorInviteListView(APIView):
    """
    GET /courses/<pk>/instructors/invites/
    Owner lists all invites for a course. Supports ?status= filter.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]

    def get(self, request, pk):
        # Numeric-ID endpoint: non-owners (including co-instructors) get 404.
        try:
            course = NidusCourse.objects.get(pk=pk, created_by=request.user)
        except NidusCourse.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        qs = CourseInstructorInvite.objects.filter(course=course).select_related(
            'invited_by', 'invited_user'
        )

        status_filter = request.query_params.get('status')
        if status_filter:
            valid = {s for s, _ in CourseInstructorInvite.STATUS_CHOICES}
            if status_filter not in valid:
                return Response(
                    {'success': False, 'message': f'Invalid status. Choices: {", ".join(sorted(valid))}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(status=status_filter)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = CourseInstructorInviteOwnerSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class CourseInstructorInviteRevokeView(APIView):
    """
    DELETE /courses/<pk>/instructors/invites/<invite_id>/
    Owner revokes a pending invite.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]

    def delete(self, request, pk, invite_id):
        # Numeric-ID endpoint: non-owners get 404.
        try:
            course = NidusCourse.objects.get(pk=pk, created_by=request.user)
        except NidusCourse.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            invite = CourseInstructorInvite.objects.get(pk=invite_id, course=course)
        except CourseInstructorInvite.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Invite not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            revoke_instructor_invite(invite, request.user)
        except InviteError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )

        return Response({'success': True, 'message': 'Invite revoked.'}, status=status.HTTP_200_OK)


class MyInviteListView(APIView):
    """
    GET /courses/invites/my/
    Instructor lists their received invites. Defaults to pending; use ?status= for others.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def get(self, request):
        qs = CourseInstructorInvite.objects.filter(
            invited_user=request.user
        ).select_related('course', 'invited_by')

        status_filter = request.query_params.get('status', CourseInstructorInvite.STATUS_PENDING)
        valid = {s for s, _ in CourseInstructorInvite.STATUS_CHOICES}
        if status_filter not in valid:
            return Response(
                {'success': False, 'message': f'Invalid status. Choices: {", ".join(sorted(valid))}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = qs.filter(status=status_filter)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = CourseInstructorInviteSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class InviteAcceptView(APIView):
    """
    POST /courses/invites/<token>/accept/
    Invitee accepts the invite; atomically added to course.instructors.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def post(self, request, token):
        try:
            invite = accept_instructor_invite(token=token, user=request.user)
        except CourseInstructorInvite.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Invite not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except InviteError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )
        except Exception as exc:
            logger.error('InviteAcceptView: unexpected error: %s', exc, exc_info=True)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': f'You have joined "{invite.course.title}" as a co-instructor.',
                'data': CourseInstructorInviteSerializer(invite).data,
            },
            status=status.HTTP_200_OK,
        )


class InviteDeclineView(APIView):
    """
    POST /courses/invites/<token>/decline/
    Invitee declines the invite.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def post(self, request, token):
        try:
            invite = decline_instructor_invite(token=token, user=request.user)
        except CourseInstructorInvite.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Invite not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except InviteError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=exc.http_status,
            )
        except Exception as exc:
            logger.error('InviteDeclineView: unexpected error: %s', exc, exc_info=True)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': f'You have declined the invitation to "{invite.course.title}".',
                'data': CourseInstructorInviteSerializer(invite).data,
            },
            status=status.HTTP_200_OK,
        )
