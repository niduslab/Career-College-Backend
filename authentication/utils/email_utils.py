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
