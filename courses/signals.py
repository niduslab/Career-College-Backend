from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from courses.models import CourseReview, Enrollment, WatchProgress
from courses.services.enrollment_service import recalculate_progress


@receiver(pre_save, sender=WatchProgress)
def cache_previous_watch_completion_state(sender, instance, **kwargs):
    """Cache previous is_completed so post_save skips recalc when only watched_seconds changes."""
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

        newly_completed = instance.is_completed and not previous_is_completed
        if newly_completed:
            _user = instance.user
            _lecture_id = instance.lecture_id
            _lecture_title = instance.lecture.title
            _course_title = enrollment.course.title
            _course_slug = instance.lecture.section.course.slug

            def _notify_lecture_completed():
                from notifications.models import NotificationEventType
                from notifications.services.dispatcher import dispatch
                dispatch(
                    NotificationEventType.LECTURE_COMPLETED,
                    [_user],
                    context={
                        'lecture_id': _lecture_id,
                        'lecture_title': _lecture_title,
                        'course_title': _course_title,
                        'course_slug': _course_slug,
                    },
                    skip_email=True,
                )

            transaction.on_commit(_notify_lecture_completed)


@receiver(post_delete, sender=CourseReview)
def recalculate_course_avg_on_review_delete(sender, instance, **kwargs):
    """Keep avg_rating/review_count fresh for cascade-deletes that bypass delete_review()."""
    from courses.services.review_service import _recalculate_course_avg
    course_id = instance.course_id
    transaction.on_commit(lambda: _recalculate_course_avg(course_id))
