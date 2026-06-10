import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _invite_urls(token):
    base = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
    return {'view_url': f'{base}/invites/{str(token)}'}


def send_instructor_invite_email(invite):
    """Send a co-instructor invitation email to the invitee."""
    expiry_days = getattr(settings, 'INSTRUCTOR_INVITE_EXPIRY_DAYS', 7)
    urls = _invite_urls(invite.token)

    html_message = render_to_string(
        'emails/instructor_invite.html',
        {
            'invitee_name': invite.invited_user.full_name,
            'inviter_name': invite.invited_by.full_name,
            'course_title': invite.course.title,
            'token': str(invite.token),
            'expiry_days': expiry_days,
            **urls,
        },
    )
    plain_message = (
        f"Hi {invite.invited_user.full_name},\n\n"
        f"{invite.invited_by.full_name} has invited you to co-instruct\n"
        f'"{invite.course.title}" on Career College.\n\n'
        f"View invitation: {urls['view_url']}\n\n"
        f"This invite expires in {expiry_days} day(s).\n\n"
        f"If you were not expecting this invitation you can safely ignore this email.\n\n"
        f"Career College Team"
    )
    try:
        send_mail(
            subject=f'{invite.invited_by.full_name} invited you to co-instruct "{invite.course.title}"',
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invite.invited_user.email],
            html_message=html_message,
            fail_silently=False,
        )
    except Exception as exc:
        logger.error(
            'send_instructor_invite_email: failed to send to %s (invite=%s): %s',
            invite.invited_user.email,
            invite.pk,
            exc,
            exc_info=True,
        )
        raise
