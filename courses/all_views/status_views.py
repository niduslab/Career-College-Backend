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

from core.permissions import (
    IsPlatformAdmin,
    IsEmailVerified,
    IsVerifiedCourseCreator,
    IsVerifiedPartnerInstitution,
)
from courses.all_models.schedule_models import CourseSchedule
from courses.all_serializers.schedule_serializers import CourseAdminReviewDetailSerializer
from courses.models import NidusCourse
from courses.serializers import NidusCourseSerializer
from courses.services.schedule_service import activate_schedule

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


class CourseMarkFinishedView(APIView):
    """
    POST {pk}/finish/ — expert marks an institution-owned course complete.

    draft → institution_review. Expert-only (scoped by instructors=request.user;
    the institution user is created_by, not in instructors → 404). Individual
    (non-institution) courses → 422 (they use /submit/ instead).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        course = get_object_or_404(
            NidusCourse.objects.filter(instructors=request.user).distinct(),
            pk=pk,
        )

        if not course.partner_institution_id:
            return Response(
                {'success': False,
                 'message': 'This is not an institution-owned course. Use /submit/ to send it for review.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        try:
            course.transition_to('institution_review')
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
        _expert_name = request.user.get_full_name() or request.user.email
        _institution_user_id = course.partner_institution.user_id

        def _notify_institution():
            from authentication.models import User
            from notifications.models import NotificationEventType
            from notifications.services.dispatcher import dispatch
            institution_user = User.objects.filter(pk=_institution_user_id).first()
            if institution_user:
                dispatch(
                    NotificationEventType.COURSE_MARKED_FINISHED,
                    [institution_user],
                    context={
                        'course_id': _course_id,
                        'course_title': _course_title,
                        'expert_name': _expert_name,
                    },
                )

        transaction.on_commit(_notify_institution)

        return Response(
            {
                'success': True,
                'message': 'Course marked as finished and sent to your institution for review.',
                'data': NidusCourseSerializer(course).data,
            },
            status=status.HTTP_200_OK,
        )


class CourseInstitutionReviewView(APIView):
    """
    POST {pk}/institution-review/ — the owning institution acts on a course an
    expert finished. Body: {action: "submit" | "send_back", rejection_reason?}.

    submit    → institution_review → under_review   (forward to platform admin)
    send_back → institution_review → rejected        (return to expert, reason required)

    Institution-only, scoped to the owning institution (404 on no-access).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        institution = request.user.partner_institution_profile
        course = get_object_or_404(
            NidusCourse.objects.filter(partner_institution=institution),
            pk=pk,
        )

        action = request.data.get('action', '').strip().lower()
        if action not in ('submit', 'send_back'):
            return Response(
                {'success': False, 'message': 'action must be "submit" or "send_back".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rejection_reason = request.data.get('rejection_reason', '')
        new_status = 'under_review' if action == 'submit' else 'rejected'

        try:
            course.transition_to(
                new_status,
                reviewer=request.user,
                rejection_reason=rejection_reason,
            )
        except ValidationError as e:
            if hasattr(e, 'message_dict'):
                return Response(
                    {'success': False, 'message': 'Institution review action failed.', 'errors': e.message_dict},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': e.messages[0]},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        _course_id = course.pk
        _course_title = course.title
        _course_slug = course.slug
        _institution_name = institution.institution_name
        _instructor_name = request.user.get_full_name() or request.user.email
        _rejection_reason = rejection_reason
        _action = action
        _instructors_snapshot = list(course.instructors.all())

        def _notify():
            from authentication.models import User
            from notifications.models import NotificationEventType
            from notifications.services.dispatcher import dispatch
            if _action == 'submit':
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
            else:
                dispatch(
                    NotificationEventType.COURSE_SENT_BACK,
                    _instructors_snapshot,
                    context={
                        'course_title': _course_title,
                        'course_slug': _course_slug,
                        'institution_name': _institution_name,
                        'rejection_reason': _rejection_reason,
                    },
                )

        transaction.on_commit(_notify)

        message = (
            'Course submitted to the platform admin for review.'
            if action == 'submit'
            else 'Course sent back to the expert for changes.'
        )
        return Response(
            {'success': True, 'message': message, 'data': NidusCourseSerializer(course).data},
            status=status.HTTP_200_OK,
        )


class CourseAdminReviewView(APIView):

    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get(self, request, pk):
        course = get_object_or_404(
            NidusCourse.objects
            .select_related('created_by', 'category', 'partner_institution')
            .prefetch_related('instructors', 'schedules', 'sections__contents'),
            pk=pk,
        )
        return Response(
            {'success': True, 'data': CourseAdminReviewDetailSerializer(course).data},
            status=status.HTTP_200_OK,
        )

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

        _stale_schedule_labels = []
        if action == 'approve':
            for schedule in course.schedules.filter(status=CourseSchedule.Status.DRAFT):
                try:
                    activate_schedule(schedule, request.user)
                except Exception:
                    logger.exception(
                        'Failed to auto-activate schedule %s for course %s on approval',
                        schedule.pk, course.pk,
                    )
                    _stale_schedule_labels.append(schedule.cohort_label or f'Schedule {schedule.pk}')

        _course_title = course.title
        _course_slug = course.slug
        _rejection_reason = rejection_reason
        _action = action
        _instructors_snapshot = list(course.instructors.all())
        _schedule_owner_id = course.partner_institution.user_id if course.partner_institution_id else None

        def _notify_review_decision():
            from notifications.models import NotificationEventType
            from notifications.services.dispatcher import dispatch
            if _action == 'approve':
                dispatch(
                    NotificationEventType.COURSE_APPROVED,
                    _instructors_snapshot,
                    context={'course_title': _course_title, 'course_slug': _course_slug},
                )
                if _stale_schedule_labels:
                    from authentication.models import User
                    if _schedule_owner_id is not None:
                        owner = User.objects.filter(pk=_schedule_owner_id).first()
                        recipients = [owner] if owner else []
                    else:
                        recipients = _instructors_snapshot
                    if recipients:
                        dispatch(
                            NotificationEventType.COURSE_SCHEDULE_NEEDS_ATTENTION,
                            recipients,
                            context={
                                'course_title': _course_title,
                                'course_slug': _course_slug,
                                'schedule_labels': _stale_schedule_labels,
                            },
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
