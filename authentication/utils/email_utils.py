import logging

from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

def send_otp_email(user, otp_code, purpose='registration'):
    """
    Send OTP verification email to user
    Args:
        user: User instance
        otp_code: OTP code to send
        purpose: 'registration' or 'password_reset'
    """
    if purpose == 'registration':
        subject = 'Email Verification - Career College'
        greeting = 'Welcome to Career College!'
        message = 'Thank you for registering with Career College. Please verify your email address using the OTP below:'
    else:
        subject = 'Password Reset - Career College'
        greeting = 'Password Reset Request'
        message = 'You requested to reset your password. Please use the OTP below to proceed:'
    
    # Render HTML email from template
    html_message = render_to_string(
        'emails/send_email.html',
        {
            'greeting': greeting,
            'message': message,
            'otp_code': otp_code,
        }
    )
    
    plain_message = f"""
    {greeting}
    
    Hello,
    
    {message}
    
    OTP: {otp_code}
    
    ⚠️ This OTP will expire in 2 minutes.
    
    If you didn't make this request, please ignore this email or contact support.
    
    Best regards,
    Career College Team
    """
    
    try:
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        return True
    except Exception as e:
        logger.error(
            f"Failed to send OTP email to {user.email} for purpose={purpose}: {e}",
            exc_info=True,
        )
        return False


def send_credentials_email(user, password, institution_name=None):
    """
    Email an institution-onboarded expert their login credentials (email +
    preset password) so they can log in immediately.

    Used in place of the OTP activation flow: the verified institution vouches
    for the expert, so no email-ownership proof is required.

    Returns True on success, False on failure (logged, never raised).
    """
    subject = 'Your Career College Instructor Account'
    login_url = f"{settings.FRONTEND_URL}/login"

    html_message = render_to_string(
        'emails/expert_credentials.html',
        {
            'full_name': user.full_name,
            'email': user.email,
            'password': password,
            'login_url': login_url,
            'institution_name': institution_name,
        },
    )

    affiliation = f" by {institution_name}" if institution_name else ""
    plain_message = f"""
    Welcome to Career College!

    Hello {user.full_name},

    An instructor account has been created for you{affiliation}. You can log in
    right away using the credentials below:

    Email: {user.email}
    Password: {password}

    Log in here: {login_url}

    For your security, please change your password after your first login.

    Best regards,
    Career College Team
    """

    try:
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        return True
    except Exception as e:
        logger.error(
            f"Failed to send credentials email to {user.email}: {e}",
            exc_info=True,
        )
        return False
