import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from courses.models import (
    AssignmentSubmission,
    Enrollment,
    NidusCourse,
    QuizAttempt,
    SectionContent,
    WatchProgress,
)

logger = logging.getLogger(__name__)

# `update_last_accessed` is called on every learner consumption GET; debounce
# so we don't write a row on every page refresh. 5 minutes of staleness is
# acceptable for "last opened the course" — nobody needs second-level precision.
LAST_ACCESSED_DEBOUNCE = timedelta(minutes=5)


def get_catalog_courses() -> QuerySet[NidusCourse]:
    """Return published courses for the public catalog."""
    return (
        NidusCourse.objects
        .filter(is_published=True)
        .select_related('created_by', 'category')
        .prefetch_related('instructors')
        .order_by('-published_at')
    )


def get_learner_enrollments(user) -> QuerySet[Enrollment]:
    """Return active enrollments for a learner, most recently accessed first."""
    return (
        Enrollment.objects
        .filter(user=user, is_active=True)
        .select_related('course__created_by', 'course__category')
        .prefetch_related('course__instructors')
        .order_by(F('last_accessed_at').desc(nulls_last=True), '-created_at')
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
    Completion rules:
    - lecture: WatchProgress.is_completed=True
    - quiz: at least one QuizAttempt exists for the learner
    - assignment: reserved for AssignmentSubmission(status='passed')
    - coding: reserved for CodingSubmission(status='accepted')

    Notes:
    - Uses grouped queries + set intersections to avoid N+1 behavior.
    - Assignment/coding completion remains zero until learner submission models exist.
    """
    course = enrollment.course

    content_rows = list(
        SectionContent.objects
        .filter(section__course=course)
        .values_list('item_type', 'object_id')
    )
    total_items = len(content_rows)

    if total_items == 0:
        enrollment.progress_percent = 0
        enrollment.save(update_fields=['progress_percent', 'updated_at'])
        return enrollment

    lecture_ids = {
        object_id
        for item_type, object_id in content_rows
        if item_type == SectionContent.ItemType.LECTURE
    }
    if lecture_ids:
        completed_lecture_ids = set(
            WatchProgress.objects.filter(
                user=enrollment.user,
                lecture_id__in=lecture_ids,
                is_completed=True,
            ).values_list('lecture_id', flat=True)
        )
        completed_lectures = len(completed_lecture_ids)
    else:
        completed_lectures = 0

    quiz_ids = {
        object_id
        for item_type, object_id in content_rows
        if item_type == SectionContent.ItemType.QUIZ
    }
    if quiz_ids:
        completed_quiz_ids = set(
            QuizAttempt.objects.filter(
                user=enrollment.user,
                quiz_id__in=quiz_ids,
            ).values_list('quiz_id', flat=True)
        )
        completed_quizzes = len(completed_quiz_ids)
    else:
        completed_quizzes = 0

    assignment_ids = {
        object_id
        for item_type, object_id in content_rows
        if item_type == SectionContent.ItemType.ASSIGNMENT
    }
    if assignment_ids:
        completed_assignment_ids = set(
            AssignmentSubmission.objects.filter(
                user=enrollment.user,
                assignment_id__in=assignment_ids,
                status=AssignmentSubmission.Status.PASSED,
            ).values_list('assignment_id', flat=True)
        )
        completed_assignments = len(completed_assignment_ids)
    else:
        completed_assignments = 0

    # Coding completion still reserved — lands with CodingSubmission.
    completed_coding = 0

    completed_items = (
        completed_lectures
        + completed_quizzes
        + completed_assignments
        + completed_coding
    )
    progress = min(int((completed_items / total_items) * 100), 100)

    update_fields = ['progress_percent', 'updated_at']
    enrollment.progress_percent = progress
    if progress >= 100 and enrollment.completed_at is None:
        enrollment.completed_at = timezone.now()
        update_fields.append('completed_at')
    elif progress < 100 and enrollment.completed_at is not None:
        enrollment.completed_at = None
        update_fields.append('completed_at')

    enrollment.save(update_fields=update_fields)
    return enrollment


def update_last_accessed(enrollment: Enrollment):
    """Touch the last_accessed_at timestamp, debounced to LAST_ACCESSED_DEBOUNCE.

    Skips the write when the previous touch is younger than the debounce
    window. Avoids a row-level UPDATE on every page refresh / progress ping.
    """
    now = timezone.now()
    if enrollment.last_accessed_at is not None and (
        now - enrollment.last_accessed_at < LAST_ACCESSED_DEBOUNCE
    ):
        return enrollment.last_accessed_at

    Enrollment.objects.filter(pk=enrollment.pk).update(
        last_accessed_at=now,
        updated_at=now,
    )
    enrollment.last_accessed_at = now
    return now
