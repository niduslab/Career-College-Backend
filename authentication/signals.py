import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from authentication.models import User
from authentication.services.profile_service import ensure_profile_for_type

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Auto-create the appropriate profile when a new user is created."""
    if not created:
        return
    ensure_profile_for_type(instance)
