import logging

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, F, QuerySet
from django.utils import timezone

from courses.all_models.course_models import NidusCourse
from courses.all_models.review_models import CourseReview, ReviewVote

logger = logging.getLogger(__name__)


class ReviewError(Exception):
    """Raised by review service functions for domain-rule violations."""

    def __init__(self, message: str, http_status: int = 422):
        self.message = message
        self.http_status = http_status
        super().__init__(message)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_or_update_review(user, course: NidusCourse, validated_data: dict) -> tuple:
    """Upsert a review for an enrolled learner.

    Returns (review, created: bool). Dispatches avg_rating recalc on commit.

    Raises:
        ReviewError(403) — caller is not actively enrolled in the course.
    """
    from courses.all_models.enrollment_models import Enrollment

    enrollment = (
        Enrollment.objects
        .filter(user=user, course=course, is_active=True)
        .first()
    )
    if enrollment is None:
        raise ReviewError(
            'You must be enrolled in this course to leave a review.',
            http_status=403,
        )

    with transaction.atomic():
        review, created = CourseReview.objects.update_or_create(
            enrollment=enrollment,
            defaults={
                'user': user,
                'course': course,
                **validated_data,
            },
        )
        course_id = course.pk
        transaction.on_commit(lambda: _recalculate_course_avg(course_id))

    return review, created


def delete_review(user, course: NidusCourse) -> None:
    """Delete a learner's own review. Dispatches avg_rating recalc on commit.

    Raises:
        ReviewError(404) — no review exists for this (user, course) pair.
    """
    try:
        review = CourseReview.objects.get(user=user, course=course)
    except CourseReview.DoesNotExist:
        raise ReviewError('Review not found.', http_status=404)

    review_id = review.pk
    course_id = course.pk

    with transaction.atomic():
        review.delete()
        transaction.on_commit(lambda: _recalculate_course_avg(course_id))



def vote_on_review(voter, review_id: int, is_helpful: bool) -> ReviewVote:
    """Upsert a helpful/not-helpful vote. Flips if already voted opposite.

    Raises:
        ReviewError(404) — review does not exist or is not published.
        ReviewError(422) — voter is the review's own author.
    """
    try:
        review = CourseReview.objects.get(pk=review_id, is_published=True)
    except CourseReview.DoesNotExist:
        raise ReviewError('Review not found.', http_status=404)

    if review.user_id == voter.pk:
        raise ReviewError('You cannot vote on your own review.', http_status=422)

    with transaction.atomic():
        existing = ReviewVote.objects.select_for_update().filter(
            review=review, voter=voter
        ).first()

        if existing is not None and existing.is_helpful == is_helpful:
            return existing  # idempotent — same direction, no-op

        if existing is not None:
            # Vote flip: decrement old counter, flip flag, increment new counter.
            old_field = 'helpful_count' if existing.is_helpful else 'not_helpful_count'
            new_field = 'helpful_count' if is_helpful else 'not_helpful_count'
            CourseReview.objects.filter(pk=review.pk).update(
                **{
                    old_field: F(old_field) - 1,
                    new_field: F(new_field) + 1,
                    'updated_at': timezone.now(),
                }
            )
            existing.is_helpful = is_helpful
            existing.save(update_fields=['is_helpful'])
            return existing

        # New vote — nested savepoint guards against concurrent first-vote race.
        # If two requests both find existing=None, the second hits the unique
        # constraint; we catch IntegrityError, roll back only the savepoint, and
        # return the vote the winning request created (it also updated the count).
        try:
            with transaction.atomic():
                vote = ReviewVote.objects.create(review=review, voter=voter, is_helpful=is_helpful)
        except IntegrityError:
            return ReviewVote.objects.get(review=review, voter=voter)

        count_field = 'helpful_count' if is_helpful else 'not_helpful_count'
        CourseReview.objects.filter(pk=review.pk).update(
            **{count_field: F(count_field) + 1, 'updated_at': timezone.now()}
        )
        return vote


def get_course_reviews(course: NidusCourse, params, requesting_user=None) -> QuerySet:
    """Return a filtered, ordered queryset of published reviews for a course.

    Supported params:
        ?rating=<1-5>         exact star filter
        ?ordering=            -created_at (default) | created_at | -helpful_count | -rating | rating
    """
    qs = (
        CourseReview.objects
        .filter(course=course, is_published=True)
        .select_related('user')
    )

    rating_param = params.get('rating')
    if rating_param is not None:
        try:
            star = int(rating_param)
        except (ValueError, TypeError):
            raise ReviewError(f'"{rating_param}" is not a valid rating.', http_status=400)
        if not (1 <= star <= 5):
            raise ReviewError('Rating must be between 1 and 5.', http_status=400)
        qs = qs.filter(rating=star)

    _VALID_ORDERINGS = {
        '-created_at', 'created_at',
        '-helpful_count',
        '-rating', 'rating',
    }
    ordering = params.get('ordering', '-created_at')
    if ordering not in _VALID_ORDERINGS:
        ordering = '-created_at'

    return qs.order_by(ordering, '-id')


def get_review_summary(course: NidusCourse) -> dict:
    """Return avg_rating, review_count, and 1–5 star distribution for a course.

    One DB round-trip: per-star counts are computed in Python from the five rows.
    """
    qs = CourseReview.objects.filter(course=course, is_published=True)
    distribution = {str(i): 0 for i in range(1, 6)}
    total_count = 0
    weighted_sum = 0
    for row in qs.values('rating').annotate(n=Count('id')):
        distribution[str(row['rating'])] = row['n']
        total_count += row['n']
        weighted_sum += row['rating'] * row['n']

    avg = round(weighted_sum / total_count, 2) if total_count else 0.0
    return {
        'avg_rating': avg,
        'review_count': total_count,
        'distribution': distribution,
    }


def get_my_review(user, course: NidusCourse) -> CourseReview:
    """Fetch the caller's own review for a course.

    Raises CourseReview.DoesNotExist if none exists.
    """
    return CourseReview.objects.get(user=user, course=course)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _recalculate_course_avg(course_id: int) -> None:
    """Recompute avg_rating and review_count on NidusCourse from published reviews.

    Called via transaction.on_commit so it never fires on a rolled-back write.
    Uses a single aggregate query + a targeted UPDATE (no full model save).
    """
    agg = CourseReview.objects.filter(
        course_id=course_id, is_published=True
    ).aggregate(avg=Avg('rating'), count=Count('id'))

    avg = round(float(agg['avg'] or 0), 2)
    count = agg['count']

    try:
        NidusCourse.objects.filter(pk=course_id).update(
            avg_rating=avg,
            review_count=count,
            updated_at=timezone.now(),
        )
    except Exception:
        logger.error('avg_rating recalc failed for course=%s', course_id, exc_info=True)
