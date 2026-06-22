import logging
import secrets

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)

# Sentinel: distinguishes "field omitted" from "explicitly set to None/clear".
_UNSET = object()


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
        .select_related('user', 'department')
        .annotate(_course_count=Count('user__instructed_nidus_courses', distinct=True))
        .order_by('-affiliated_at')
    )


def provision_expert(institution_profile, *, full_name, email, bio='',
                     specialization=None, headline='', department_id=None):
    """
    Create an instructor account owned by *institution_profile* with a preset
    password and email the expert their login credentials (email + password) so
    they can log in immediately.

    The account is created with ``is_email_verified=True`` and the profile with
    ``is_verified=True`` because the verified institution vouches for the expert
    — no OTP email-ownership proof or separate identity verification is required.

    Raises ExpertError on any validation failure.
    """
    from authentication.models import User

    email = email.strip().lower()
    full_name = full_name.strip()
    if not full_name:
        raise ExpertError('Full name is required.')

    if User.objects.all_with_deleted().filter(email__iexact=email).exists():
        raise ExpertError('A user with this email already exists.', http_status=422)

    specialization = specialization or []

    # Validate the department belongs to this institution before any writes.
    from authentication.services.department_service import resolve_expert_department
    department = resolve_expert_department(institution_profile, department_id)

    # Preset password emailed to the expert below; they can log in immediately.
    password = secrets.token_urlsafe(9)

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=password,
                full_name=full_name,
                user_type='instructor',
                is_email_verified=True,
            )
            profile = _attach_to_institution(
                user, institution_profile, bio, headline, specialization, department,
            )
    except IntegrityError:
        # Lost a race against a concurrent provisioning of the same email.
        raise ExpertError('A user with this email already exists.', http_status=422)

    _institution_name = institution_profile.institution_name

    # Credentials email — sent asynchronously via Celery. Enqueued on_commit so a
    # rolled-back account never leaks a phantom task into the queue. The password
    # is passed as a task arg (never persisted except as the hash on the user)
    # and is deliberately kept out of the notification payload below.
    _user_pk = user.pk

    def _enqueue_credentials_email():
        from authentication.tasks import send_expert_credentials_email_task
        send_expert_credentials_email_task.delay(_user_pk, password, _institution_name)

    transaction.on_commit(_enqueue_credentials_email)

    def _notify_expert():
        from notifications.models import NotificationEventType
        from notifications.services.dispatcher import dispatch
        dispatch(
            NotificationEventType.EXPERT_ONBOARDED,
            [user],
            context={'institution_name': _institution_name},
            skip_email=True,  # credentials email already sent above; no password in this payload
        )

    transaction.on_commit(_notify_expert)

    return profile


def _attach_to_institution(user, institution_profile, bio, headline, specialization, department=None):
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
    profile.department = department  # Department instance or None
    profile.save(update_fields=[
        'affiliated_institution', 'onboarding_source', 'affiliation_status',
        'affiliated_at', 'is_verified', 'bio', 'headline', 'specialization',
        'department', 'updated_at',
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


def update_expert(profile, *, bio=None, headline=None, specialization=None, department_id=_UNSET):
    """Edit an affiliated expert's profile details.

    `department_id` uses a sentinel default: omit it to leave the department
    untouched; pass `None` to clear it; pass an id to reassign (validated against
    the expert's own institution).
    """
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
    if department_id is not _UNSET:
        from authentication.services.department_service import resolve_expert_department
        profile.department = resolve_expert_department(
            profile.affiliated_institution, department_id,
        )
        updates.append('department')
    if updates:
        updates.append('updated_at')
        profile.save(update_fields=updates)
    return profile
