import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class InviteError(Exception):
    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.http_status = http_status


def _expiry_days():
    return int(getattr(settings, 'INSTRUCTOR_INVITE_EXPIRY_DAYS', 7))


def create_instructor_invite(course, owner, email):
    """
    Validate and create a pending CourseInstructorInvite.
    Dispatches the email task on commit.
    Raises InviteError on any validation failure.
    """
    from authentication.models import User
    from courses.models import CourseInstructorInvite
    from courses.tasks import send_instructor_invite_email_task

    if course.created_by_id != owner.pk:
        raise InviteError('Only the course owner can send invites.', http_status=403)

    try:
        invited_user = User.objects.get(
            email__iexact=email,
            user_type='instructor',
            is_deleted=False,
            is_email_verified=True,
        )
    except User.DoesNotExist:
        raise InviteError('No verified instructor found with this email.')

    if invited_user.pk == owner.pk:
        raise InviteError('You cannot invite yourself.')

    if course.instructors.filter(pk=invited_user.pk).exists():
        raise InviteError('This user is already an instructor on this course.')

    if CourseInstructorInvite.objects.filter(
        course=course,
        invited_user=invited_user,
        status=CourseInstructorInvite.STATUS_PENDING,
    ).exists():
        raise InviteError('A pending invite already exists for this user.')

    with transaction.atomic():
        invite = CourseInstructorInvite.objects.create(
            course=course,
            invited_by=owner,
            invited_user=invited_user,
            expires_at=timezone.now() + timedelta(days=_expiry_days()),
        )
        transaction.on_commit(lambda: send_instructor_invite_email_task.delay(invite.pk))

    return invite


def revoke_instructor_invite(invite, owner):
    """
    Revoke a pending invite. Owner-only.
    Raises InviteError on failure.
    """
    from courses.models import CourseInstructorInvite

    if invite.course.created_by_id != owner.pk:
        raise InviteError('Only the course owner can revoke invites.', http_status=403)

    with transaction.atomic():
        invite = CourseInstructorInvite.objects.select_for_update().get(pk=invite.pk)

        if invite.status != invite.STATUS_PENDING:
            raise InviteError('Only pending invites can be revoked.', http_status=422)

        invite.status = invite.STATUS_REVOKED
        invite.responded_at = timezone.now()
        invite.save(update_fields=['status', 'responded_at', 'updated_at'])

    return invite


def accept_instructor_invite(token, user):
    """
    Accept an invite. Atomically adds user to course.instructors M2M.
    Raises CourseInstructorInvite.DoesNotExist when token not found or not for this user.
    Raises InviteError(http_status=410) when invite is not actionable.
    """
    from courses.models import CourseInstructorInvite

    with transaction.atomic():
        try:
            invite = (
                CourseInstructorInvite.objects
                .select_related('course')
                .select_for_update()
                .get(token=token, invited_user=user)
            )
        except CourseInstructorInvite.DoesNotExist:
            raise

        _assert_actionable(invite)

        if not invite.course.is_editable():
            raise InviteError(
                'This course is no longer accepting new instructors.',
                http_status=422,
            )

        invite.status = CourseInstructorInvite.STATUS_ACCEPTED
        invite.responded_at = timezone.now()
        invite.save(update_fields=['status', 'responded_at', 'updated_at'])
        invite.course.instructors.add(invite.invited_user)

    return invite


def decline_instructor_invite(token, user):
    """
    Decline an invite.
    Raises CourseInstructorInvite.DoesNotExist when token not found or not for this user.
    Raises InviteError(http_status=410) when invite is not actionable.
    """
    from courses.models import CourseInstructorInvite

    with transaction.atomic():
        try:
            invite = (
                CourseInstructorInvite.objects
                .select_related('course')
                .select_for_update()
                .get(token=token, invited_user=user)
            )
        except CourseInstructorInvite.DoesNotExist:
            raise

        _assert_actionable(invite)

        invite.status = CourseInstructorInvite.STATUS_DECLINED
        invite.responded_at = timezone.now()
        invite.save(update_fields=['status', 'responded_at', 'updated_at'])

    return invite


def _assert_actionable(invite):
    """Raise InviteError(410) if invite cannot be acted on.

    Does NOT mutate the invite row — all callers run inside transaction.atomic(), so
    any save here would be rolled back when InviteError propagates up. Expiry DB
    writes are owned by expire_instructor_invites_task (runs hourly via Celery Beat).
    """
    from courses.models import CourseInstructorInvite

    if invite.expires_at <= timezone.now():
        raise InviteError('This invite has expired.', http_status=410)

    if invite.status != CourseInstructorInvite.STATUS_PENDING:
        raise InviteError('This invite is no longer valid.', http_status=410)
