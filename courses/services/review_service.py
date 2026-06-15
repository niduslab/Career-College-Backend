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



def create_or_update_review(user, course: NidusCourse, validated_data: dict) -> tuple:
    """Upsert a review. Returns (review, created). Raises ReviewError(403) if not enrolled."""
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

        if created:
            _review_id = review.pk
            _review_rating = review.rating
            _course_title = course.title
            _course_slug = course.slug
            _course_pk = course.pk
            transaction.on_commit(lambda: _dispatch_review_received(
                _course_pk, _course_title, _course_slug, _review_id, _review_rating,
            ))

    return review, created


def delete_review(user, course: NidusCourse) -> None:
    """Delete a learner's own review. Raises ReviewError(404) if not found."""
    try:
        review = CourseReview.objects.get(user=user, course=course)
    except CourseReview.DoesNotExist:
        raise ReviewError('Review not found.', http_status=404)

    review_id = review.pk
    course_id = course.pk

    with transaction.atomic():
        review.delete()
        transaction.on_commit(lambda: _recalculate_course_avg(course_id))



def _dispatch_review_received(course_pk, course_title, course_slug, review_id, rating):
    from courses.all_models.course_models import NidusCourse
    from notifications.models import NotificationEventType
    from notifications.services.dispatcher import dispatch
    try:
        course = NidusCourse.objects.prefetch_related('instructors').get(pk=course_pk)
        instructors = list(course.instructors.all())
        if instructors:
            dispatch(
                NotificationEventType.REVIEW_RECEIVED,
                instructors,
                context={
                    'course_title': course_title,
                    'course_slug': course_slug,
                    'review_id': review_id,
                    'rating': rating,
                },
                skip_email=True,
            )
    except Exception:
        logger.warning('REVIEW_RECEIVED dispatch failed for course=%s', course_pk)


def vote_on_review(voter, review_id: int, is_helpful: bool) -> ReviewVote:
    """Upsert a vote; flips direction if already voted opposite. Raises ReviewError(404/422)."""
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

        # Nested savepoint guards concurrent first-vote race on the unique constraint.
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
    """Return filtered, ordered published reviews. Supports ?rating= and ?ordering=."""
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
    """Return avg_rating, review_count, and 1–5 star distribution."""
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
    """Fetch the caller's own review. Raises DoesNotExist if none."""
    return CourseReview.objects.get(user=user, course=course)



def _recalculate_course_avg(course_id: int) -> None:
    """Recompute avg_rating and review_count from published reviews. Called via on_commit only."""
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
