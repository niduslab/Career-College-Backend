from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from courses.all_models.course_models import NidusCourse, TimestampedModel


class CourseReview(TimestampedModel):
    """One review per enrolled learner per course.

    The OneToOne on `enrollment` is the primary uniqueness guarantee.
    The UniqueConstraint on `(user, course)` is a belt-and-braces defence
    against concurrent creates that slip past the application layer.
    """

    enrollment = models.OneToOneField(
        'courses.Enrollment',
        on_delete=models.CASCADE,
        related_name='review',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_reviews',
    )
    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='reviews',
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    headline = models.CharField(max_length=150)
    body = models.TextField(blank=True, default='')
    is_published = models.BooleanField(default=True, db_index=True)
    helpful_count = models.PositiveIntegerField(default=0)
    not_helpful_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'course_reviews'
        verbose_name = 'Course Review'
        verbose_name_plural = 'Course Reviews'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'course'],
                name='uq_review_user_course',
            ),
            models.CheckConstraint(
                check=models.Q(rating__gte=1) & models.Q(rating__lte=5),
                name='chk_review_rating_range',
            ),
            models.CheckConstraint(
                check=models.Q(helpful_count__gte=0),
                name='chk_review_helpful_count_non_negative',
            ),
            models.CheckConstraint(
                check=models.Q(not_helpful_count__gte=0),
                name='chk_review_not_helpful_count_non_negative',
            ),
        ]
        indexes = [
            models.Index(fields=['course', '-created_at'], name='idx_review_course_date'),
            models.Index(fields=['course', '-helpful_count'], name='idx_review_course_helpful'),
            models.Index(fields=['course', 'rating'], name='idx_review_course_rating'),
            models.Index(fields=['is_published', 'course'], name='idx_review_pub_course'),
        ]

    def __str__(self):
        return f'Review by {self.user_id} on {self.course_id} — {self.rating}★'


class ReviewVote(models.Model):
    """A learner's helpful / not-helpful vote on a single review.

    One vote per (review, voter) pair — upserted on flip, never duplicated.
    Users cannot vote on their own review (enforced in the service layer).
    """

    review = models.ForeignKey(
        CourseReview,
        on_delete=models.CASCADE,
        related_name='votes',
    )
    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='review_votes',
    )
    is_helpful = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'review_votes'
        verbose_name = 'Review Vote'
        verbose_name_plural = 'Review Votes'
        constraints = [
            models.UniqueConstraint(
                fields=['review', 'voter'],
                name='uq_vote_review_voter',
            ),
        ]

    def __str__(self):
        label = 'helpful' if self.is_helpful else 'not helpful'
        return f'Vote by {self.voter_id} on review {self.review_id} — {label}'
