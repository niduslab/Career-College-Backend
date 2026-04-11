import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from auth.models import User, LearnerProfile, InstructorProfile, PartnerInstitutionProfile

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
            PartnerInstitutionProfile.objects.get_or_create(
                user=instance,
                defaults={'institution_name': instance.full_name},
            )
    except Exception as e:
        logger.error(f"Failed to create profile for user {instance.pk} ({instance.user_type}): {e}")
