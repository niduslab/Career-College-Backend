from django.db.models.signals import post_save
from django.dispatch import receiver

from courses.models import Enrollment, WatchProgress
from courses.services.enrollment_service import recalculate_progress


@receiver(post_save, sender=WatchProgress)
def recalculate_enrollment_progress_on_watch_update(sender, instance, **kwargs):
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
