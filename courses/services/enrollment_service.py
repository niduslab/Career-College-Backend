import logging

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from courses.models import Enrollment, NidusCourse, SectionContent, WatchProgress

logger = logging.getLogger(__name__)


def get_catalog_courses() -> QuerySet[NidusCourse]:
    """Return published courses for the public catalog."""
    return (
        NidusCourse.objects
        .filter(is_published=True)
        .select_related('created_by', 'category')
        .prefetch_related(
            'instructors',
            'partner_institutions',
            'learning_objectives',
            'prerequisites',
            'audiences',
        )
        .order_by('-published_at')
    )


def get_learner_enrollments(user) -> QuerySet[Enrollment]:
    """Return active enrollments for a learner, most recently accessed first."""
    return (
        Enrollment.objects
        .filter(user=user, is_active=True)
        .select_related('course__created_by', 'course__category')
        .prefetch_related('course__instructors')
        .order_by('-last_accessed_at', '-created_at')
    )


@transaction.atomic
def enroll_learner(user, course: NidusCourse) -> Enrollment:
    """
    Enroll a learner in a published course.

    Raises ``ValidationError`` if the learner is already enrolled or if
    the course is not published.
    """
    if user.user_type != 'learner':
        raise ValidationError('Only learners can enroll in courses.')

    if not course.is_published:
        raise ValidationError('Enrollment is only allowed for published courses.')

    existing = (
        Enrollment.objects
        .select_for_update()
        .filter(user=user, course=course)
        .first()
    )
    now = timezone.now()
    if existing:
        if existing.is_active:
            raise ValidationError('You are already enrolled in this course.')
        existing.is_active = True
        existing.last_accessed_at = now
        existing.save(update_fields=['is_active', 'last_accessed_at', 'updated_at'])
        logger.info('Enrollment reactivated: user=%s course=%s', user.pk, course.pk)
        return existing

    try:
        enrollment = Enrollment.objects.create(
            user=user,
            course=course,
            # Payment is not integrated yet; all published courses enroll as free for now.
            enrollment_type=Enrollment.EnrollmentType.FREE,
            is_active=True,
            last_accessed_at=now,
        )
    except IntegrityError as exc:
        raise ValidationError('You are already enrolled in this course.') from exc

    logger.info('Enrollment created: user=%s course=%s', user.pk, course.pk)
    return enrollment


@transaction.atomic
def unenroll_learner(user, course: NidusCourse) -> Enrollment:
    """
    Soft-deactivate a learner's enrollment. Progress is preserved.

    Raises ``ValidationError`` if no active enrollment exists.
    """
    enrollment = Enrollment.objects.select_for_update().filter(
        user=user, course=course, is_active=True,
    ).first()

    if not enrollment:
        raise ValidationError('You are not enrolled in this course.')

    enrollment.is_active = False
    enrollment.save(update_fields=['is_active', 'updated_at'])

    logger.info('Enrollment deactivated: user=%s course=%s', user.pk, course.pk)
    return enrollment


def recalculate_progress(enrollment: Enrollment) -> Enrollment:
    """
    Recompute ``progress_percent`` from the actual content completion data.

    Formula: (completed content items / total content items) * 100
    Currently counts completed lectures (via WatchProgress).
    """
    course = enrollment.course
    total_items = SectionContent.objects.filter(section__course=course).count()

    if total_items == 0:
        enrollment.progress_percent = 0
        enrollment.save(update_fields=['progress_percent', 'updated_at'])
        return enrollment

    # Count completed lectures
    completed_lectures = WatchProgress.objects.filter(
        user=enrollment.user,
        lecture__section__course=course,
        is_completed=True,
    ).count()

    # TODO: Add quiz submission completion counts when quiz-taking is built
    # TODO: Add assignment submission completion counts when submissions are built

    completed_items = completed_lectures
    progress = min(int((completed_items / total_items) * 100), 100)

    enrollment.progress_percent = progress
    if progress >= 100 and enrollment.completed_at is None:
        enrollment.completed_at = timezone.now()
    enrollment.save(update_fields=['progress_percent', 'completed_at', 'updated_at'])

    return enrollment


def update_last_accessed(enrollment: Enrollment):
    """Touch the last_accessed_at timestamp."""
    now = timezone.now()
    Enrollment.objects.filter(pk=enrollment.pk).update(
        last_accessed_at=now,
    )
    enrollment.last_accessed_at = now
    return now
