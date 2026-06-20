import logging
import secrets

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)


class ExpertError(Exception):
    """Raised on expert-management business-rule violations. Carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.http_status = http_status


def institution_experts_qs(institution_profile):
    """All InstructorProfiles affiliated with this institution (any status)."""
    from authentication.models import InstructorProfile
    return (
        InstructorProfile.objects
        .filter(affiliated_institution=institution_profile)
        .select_related('user')
        .annotate(_course_count=Count('user__instructed_nidus_courses', distinct=True))
        .order_by('-affiliated_at')
    )


def provision_expert(institution_profile, *, full_name, email, bio='',
                     specialization=None, headline=''):
    """
    Create an instructor account owned by *institution_profile* and send an
    activation (OTP) email so the expert can verify and set a password.

    The expert is auto-verified (``InstructorProfile.is_verified=True``) because
    the verified institution vouches for them — they can author within the
    institution's courses without their own identity verification.

    Raises ExpertError on any validation failure.
    """
    from authentication.models import User
    from authentication.utils import send_otp_email

    email = email.strip().lower()
    full_name = full_name.strip()
    if not full_name:
        raise ExpertError('Full name is required.')

    if User.objects.all_with_deleted().filter(email__iexact=email).exists():
        raise ExpertError('A user with this email already exists.', http_status=422)

    specialization = specialization or []

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=secrets.token_urlsafe(16),
                full_name=full_name,
                user_type='instructor',
                is_email_verified=False,
            )
            profile = _attach_to_institution(
                user, institution_profile, bio, headline, specialization,
            )
    except IntegrityError:
        # Lost a race against a concurrent provisioning of the same email.
        raise ExpertError('A user with this email already exists.', http_status=422)

    # Activation OTP + notification — outside the create transaction so a mail
    # failure can't roll back the account.
    try:
        otp = user.generate_otp(purpose='registration')
        send_otp_email(user, otp, purpose='registration')
    except Exception:
        logger.exception('Failed to send activation email to expert %s', user.email)

    _institution_name = institution_profile.institution_name

    def _notify_expert():
        from notifications.models import NotificationEventType
        from notifications.services.dispatcher import dispatch
        dispatch(
            NotificationEventType.EXPERT_ONBOARDED,
            [user],
            context={'institution_name': _institution_name},
            skip_email=True,  # activation email already sent above
        )

    transaction.on_commit(_notify_expert)

    return profile


def _attach_to_institution(user, institution_profile, bio, headline, specialization):
    """Configure the auto-created InstructorProfile for an institution-onboarded expert."""
    profile = user.instructor_profile
    profile.affiliated_institution = institution_profile
    profile.onboarding_source = 'institution'
    profile.affiliation_status = 'active'
    profile.affiliated_at = timezone.now()
    profile.is_verified = True
    profile.bio = bio.strip()
    profile.headline = headline.strip()
    profile.specialization = specialization
    profile.save(update_fields=[
        'affiliated_institution', 'onboarding_source', 'affiliation_status',
        'affiliated_at', 'is_verified', 'bio', 'headline', 'specialization',
        'updated_at',
    ])
    return profile


def get_institution_expert(institution_profile, expert_id):
    """
    Fetch one of this institution's experts by InstructorProfile id.

    Raises InstructorProfile.DoesNotExist when missing OR owned by another
    institution (numeric id → 404, never leak existence).
    """
    from authentication.models import InstructorProfile
    return InstructorProfile.objects.select_related('user').get(
        pk=expert_id, affiliated_institution=institution_profile,
    )


def set_expert_active(institution_profile, profile, active):
    """Activate or deactivate an affiliated expert. Deactivation blocks authoring."""
    new_status = 'active' if active else 'removed'
    if profile.affiliation_status == new_status:
        return profile
    profile.affiliation_status = new_status
    # is_verified mirrors active state so a removed expert can't author.
    profile.is_verified = active
    profile.save(update_fields=['affiliation_status', 'is_verified', 'updated_at'])
    return profile


def update_expert(profile, *, bio=None, headline=None, specialization=None):
    """Edit an affiliated expert's profile details."""
    updates = []
    if bio is not None:
        profile.bio = bio.strip()
        updates.append('bio')
    if headline is not None:
        profile.headline = headline.strip()
        updates.append('headline')
    if specialization is not None:
        profile.specialization = specialization
        updates.append('specialization')
    if updates:
        updates.append('updated_at')
        profile.save(update_fields=updates)
    return profile
