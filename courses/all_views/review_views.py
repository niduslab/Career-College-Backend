"""
Course review endpoints.

Routes (all under /api/v1/courses/):
    GET  <slug>/reviews/            → CourseReviewListView  (AllowAny)
    POST <slug>/reviews/            → CourseReviewListView  (IsLearnerUser)
    GET  <slug>/reviews/summary/    → CourseReviewSummaryView (AllowAny)
    GET  <slug>/reviews/my-review/  → MyReviewView          (IsLearnerUser)
    PATCH <slug>/reviews/my-review/ → MyReviewView          (IsLearnerUser)
    DELETE <slug>/reviews/my-review/→ MyReviewView          (IsLearnerUser)
    POST reviews/<int:review_id>/vote/ → ReviewVoteView     (IsLearnerUser)

Access-denied policy (project convention):
    Slug-based endpoints  → 403 when caller lacks access.
    Numeric ID endpoints  → 404 (review_id is not public-enumerable).
"""

import logging

from django.db.models import OuterRef, Subquery
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsLearnerUser
from courses.all_models.course_models import NidusCourse
from courses.all_models.review_models import CourseReview, ReviewVote
from courses.all_serializers.review_serializers import (
    CourseReviewReadSerializer,
    CourseReviewSummarySerializer,
    CourseReviewWriteSerializer,
    ReviewVoteSerializer,
)
from courses.services.review_service import (
    ReviewError,
    create_or_update_review,
    delete_review,
    get_course_reviews,
    get_my_review,
    get_review_summary,
    vote_on_review,
)

logger = logging.getLogger(__name__)


def _get_published_course_or_404(slug: str):
    """Fetch a published course by slug or return None (caller sends 404)."""
    try:
        return NidusCourse.objects.get(slug=slug, is_published=True)
    except NidusCourse.DoesNotExist:
        return None


class CourseReviewListView(APIView):
    """
    GET  — Paginated, published reviews for a course. Public.
    POST — Create or replace the caller's review. Learner-only.

    Slug → 403 when the course is not found (we treat unpublished courses
    the same as "no access" for learners; 404 would reveal existence).
    Actually, if the course simply doesn't exist or isn't published we return
    404 because the slug itself has no public meaning without a published course.
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), IsEmailVerified(), IsLearnerUser()]
        return [AllowAny()]

    def get(self, request, slug):
        course = _get_published_course_or_404(slug)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            reviews = get_course_reviews(course, request.query_params, request.user)
        except ReviewError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )

        # Annotate viewer's vote as a single subquery — one extra DB round-trip
        # per page, not per row.
        if request.user.is_authenticated:
            viewer_vote_sq = ReviewVote.objects.filter(
                review=OuterRef('pk'), voter=request.user
            ).values('is_helpful')[:1]
            reviews = reviews.annotate(_viewer_vote=Subquery(viewer_vote_sq))

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(reviews, request)
        serializer = CourseReviewReadSerializer(
            page, many=True, context={'request': request}
        )
        paginated = paginator.get_paginated_response(serializer.data)
        paginated.data = {'success': True, 'data': paginated.data}
        return paginated

    def post(self, request, slug):
        course = _get_published_course_or_404(slug)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CourseReviewWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            review, created = create_or_update_review(
                request.user, course, serializer.validated_data
            )
        except ReviewError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Review creation failed for user=%s course=%s', request.user.pk, slug)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        http_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        message = 'Review submitted.' if created else 'Review updated.'
        return Response(
            {
                'success': True,
                'message': message,
                'data': CourseReviewReadSerializer(review, context={'request': request}).data,
            },
            status=http_status,
        )


class CourseReviewSummaryView(APIView):
    """GET — Star distribution + avg_rating for a course. Public."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        course = _get_published_course_or_404(slug)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        summary = get_review_summary(course)
        return Response(
            {
                'success': True,
                'message': 'Review summary retrieved.',
                'data': CourseReviewSummarySerializer(summary).data,
            },
            status=status.HTTP_200_OK,
        )


class MyReviewView(APIView):
    """GET / PATCH / DELETE — A learner's own review for a specific course."""

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request, slug):
        course = _get_published_course_or_404(slug)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            review = get_my_review(request.user, course)
        except CourseReview.DoesNotExist:
            return Response(
                {'success': False, 'message': 'You have not reviewed this course yet.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                'success': True,
                'data': CourseReviewReadSerializer(review, context={'request': request}).data,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, slug):
        course = _get_published_course_or_404(slug)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CourseReviewWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            review, _ = create_or_update_review(
                request.user, course, serializer.validated_data
            )
        except ReviewError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Review update failed for user=%s course=%s', request.user.pk, slug)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Review updated.',
                'data': CourseReviewReadSerializer(review, context={'request': request}).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, slug):
        course = _get_published_course_or_404(slug)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            delete_review(request.user, course)
        except ReviewError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Review deletion failed for user=%s course=%s', request.user.pk, slug)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Review deleted.'},
            status=status.HTTP_200_OK,
        )


class ReviewVoteView(APIView):
    """POST — Cast or flip a helpful/not-helpful vote on a review.

    Numeric review_id → 404 on no-access (project access-denied policy).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def post(self, request, review_id):
        serializer = ReviewVoteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            vote = vote_on_review(
                request.user,
                review_id,
                serializer.validated_data['is_helpful'],
            )
        except ReviewError as exc:
            # 404 for missing review (numeric ID — don't leak existence).
            # 422 for self-vote violation.
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Vote failed for user=%s review=%s', request.user.pk, review_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        label = 'helpful' if vote.is_helpful else 'not helpful'
        return Response(
            {'success': True, 'message': f'Marked as {label}.'},
            status=status.HTTP_200_OK,
        )
