from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from courses.models import Enrollment, WatchProgress
from courses.services.enrollment_service import recalculate_progress


@receiver(pre_save, sender=WatchProgress)
def cache_previous_watch_completion_state(sender, instance, **kwargs):
    """
    Cache previous completion flag so post_save can skip expensive recalculation
    when only non-completion fields (e.g., watched_seconds) change.
    """
    if not instance.pk:
        instance._previous_is_completed = None
        return

    instance._previous_is_completed = (
        WatchProgress.objects.filter(pk=instance.pk)
        .values_list('is_completed', flat=True)
        .first()
    )


@receiver(post_save, sender=WatchProgress)
def recalculate_enrollment_progress_on_watch_update(sender, instance, created, **kwargs):
    previous_is_completed = getattr(instance, '_previous_is_completed', None)

    # Recalculate only when completion state changes, or when created as completed.
    if created and not instance.is_completed:
        return
    if not created and previous_is_completed == instance.is_completed:
        return

    enrollment = (
        Enrollment.objects
        .select_related('course', 'user')
        .filter(
            user=instance.user,
            course=instance.lecture.section.course,
            is_active=True,
        )
        .first()
    )
    if enrollment:
        recalculate_progress(enrollment)
