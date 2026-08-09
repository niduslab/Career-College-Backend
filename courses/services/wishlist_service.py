from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import QuerySet

from courses.all_models.course_models import NidusCourse
from courses.all_models.wishlist_models import Wishlist


def get_learner_wishlist(user) -> QuerySet[Wishlist]:
    """Wishlist rows with the nested catalog card fully joined.

    Mirrors get_learner_enrollments: CatalogCourseListSerializer renders
    `category` (FK) and `instructors` (M2M), so both must be joined here or
    every row costs two extra queries.
    """
    return (
        Wishlist.objects
        .filter(user=user)
        .select_related('course__created_by', 'course__category')
        .prefetch_related('course__instructors')
        .order_by('-created_at', '-id')
    )


def add_to_wishlist(user, course: NidusCourse) -> tuple[Wishlist, bool]:
    """Idempotent add. Returns (item, created).

    Re-enforces the rules declared on Wishlist.clean(), which
    Model.objects.create() does not invoke.
    """
    if user.user_type != 'learner':
        raise ValidationError('Only learners can save courses to a wishlist.')
    if not course.is_published:
        raise ValidationError('Only published courses can be saved to a wishlist.')

    try:
        return Wishlist.objects.get_or_create(user=user, course=course)
    except IntegrityError:
        # Concurrent double-POST lost the race against uq_wishlist_user_course.
        return Wishlist.objects.get(user=user, course=course), False


def remove_from_wishlist(user, course: NidusCourse) -> bool:
    """Returns True when a row was deleted, False when it was not present."""
    deleted, _ = Wishlist.objects.filter(user=user, course=course).delete()
    return bool(deleted)


def get_wishlisted_course_ids(user, course_ids) -> set[int]:
    """Course ids from `course_ids` that `user` has wishlisted.

    One indexed lookup per page (uq_wishlist_user_course), or zero queries for
    anonymous / non-learner callers and empty pages. Feeds the
    `wishlisted_course_ids` serializer context so `is_wishlisted` costs one
    round-trip per page rather than one per row.
    """
    if not course_ids or user is None or not user.is_authenticated:
        return set()
    if user.user_type != 'learner':
        return set()
    return set(
        Wishlist.objects
        .filter(user=user, course_id__in=course_ids)
        .values_list('course_id', flat=True)
    )
