import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from authentication.models import User, LearnerProfile, InstructorProfile, PartnerInstitutionProfile

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Auto-create the appropriate profile when a new user is created."""
    if not created:
        return

    try:
        if instance.user_type == 'learner':
            LearnerProfile.objects.get_or_create(user=instance)
        elif instance.user_type == 'instructor':
            InstructorProfile.objects.get_or_create(user=instance)
        elif instance.user_type == 'partner_institution':
            # Do not seed institution_name from full_name: the slug is derived
            # from institution_name, and seeding it here would freeze the slug
            # to the person's name before the real institution name is set at
            # registration. Leave it blank; registration fills it in.
            PartnerInstitutionProfile.objects.get_or_create(user=instance)
    except Exception as e:
        logger.error(f"Failed to create profile for user {instance.pk} ({instance.user_type}): {e}")
