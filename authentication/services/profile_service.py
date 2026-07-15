import logging

from authentication.models import (
    InstructorProfile,
    LearnerProfile,
    PartnerInstitutionProfile,
)

logger = logging.getLogger(__name__)


def ensure_profile_for_type(user):
    """
    Create the profile matching ``user.user_type`` if it does not exist.

    Single source of truth for profile provisioning — used by the create-time
    ``post_save`` signal and by admin role changes (which switch ``user_type``
    after creation, when the signal no longer fires). ``get_or_create`` keeps it
    idempotent. Admin users have no profile.
    """
    try:
        if user.user_type == 'learner':
            LearnerProfile.objects.get_or_create(user=user)
        elif user.user_type == 'instructor':
            InstructorProfile.objects.get_or_create(user=user)
        elif user.user_type == 'partner_institution':
            # Do not seed institution_name from full_name: the slug is derived
            # from institution_name, and seeding it here would freeze the slug
            PartnerInstitutionProfile.objects.get_or_create(user=user)
    except Exception as e:
        logger.error(f"Failed to create profile for user {user.pk} ({user.user_type}): {e}")
