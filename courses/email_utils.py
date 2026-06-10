import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _certificate_urls(certificate_uid):
    base = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
    return {
        'view_url': f'{base}/certificates/{certificate_uid}',
        'download_url': f'{base}/api/v1/courses/certificates/{certificate_uid}/download/',
    }


def send_certificate_email(certificate):
    """Send a course-completion congratulations email with the certificate link."""
    user = certificate.enrollment.user
    urls = _certificate_urls(certificate.certificate_uid)

    html_message = render_to_string(
        'emails/certificate.html',
        {
            'learner_name': user.full_name,
            'course_title': certificate.course_title,
            'issued_at': certificate.issued_at.strftime('%B %d, %Y'),
            'certificate_uid': str(certificate.certificate_uid),
            **urls,
        },
    )
    plain_message = (
        f"Congratulations, {user.full_name}!\n\n"
        f'You have successfully completed "{certificate.course_title}" on Career College.\n\n'
        f"View your certificate: {urls['view_url']}\n\n"
        f"Career College Team"
    )
    try:
        send_mail(
            subject=f'Congratulations! You completed "{certificate.course_title}"',
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        print(f"Sent certificate email to {user.email} for certificate {certificate.pk}")
    except Exception as exc:
        logger.error(
            'send_certificate_email: failed to send to %s (certificate=%s): %s',
            user.email,
            certificate.pk,
            exc,
            exc_info=True,
        )
        raise


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
