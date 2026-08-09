"""
Learner dashboard aggregate endpoints.

Routes (all under /api/v1/courses/):
    GET  learner/dashboard/summary/  -> LearnerDashboardSummaryView
    GET  learner/activity/           -> LearnerActivityFeedView
    GET  learner/upcoming/           -> LearnerUpcomingView
    GET  learner/continue/           -> LearnerContinueView

All four are read-only aggregates over existing tables — no new counters and
no denormalized cache. See courses/services/dashboard_service.py for the
per-metric caveats that are part of the response contract.
"""

import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsLearnerUser
from courses.serializers import (
    LearnerActivityItemSerializer,
    LearnerContinueSerializer,
    LearnerSummarySerializer,
    LearnerUpcomingSerializer,
)
from courses.services import (
    get_continue_target,
    get_learner_activity_feed,
    get_learner_summary,
    get_learner_upcoming,
)

logger = logging.getLogger(__name__)


def _validation_error_response(exc):
    errors = exc.message_dict if hasattr(exc, 'message_dict') else {'detail': exc.messages}
    return Response(
        {'success': False, 'message': 'Invalid query parameters.', 'errors': errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


class LearnerDashboardSummaryView(APIView):
    """
    GET /api/v1/courses/learner/dashboard/summary/

    KPI tiles for the learner dashboard, computed from existing tables in
    seven constant queries.

    Two caveats are part of the contract. `total_learning_seconds` sums the
    furthest-watched cursor positions (WatchProgress.watched_seconds), not
    accumulated playback time — re-watching does not increase it. `day_streak`
    is derived from distinct activity dates over a 120-day window in the
    platform timezone and is flagged approximate, because
    WatchProgress.last_watched_at is auto_now and overwrites history rather
    than logging events. Total XP is intentionally absent: it requires an XP
    ledger model that does not exist yet.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        try:
            summary = get_learner_summary(request.user)
        except Exception:
            logger.exception('Dashboard summary failed for user=%s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                'success': True,
                'message': 'Dashboard summary retrieved.',
                'data': LearnerSummarySerializer(summary).data,
            },
            status=status.HTTP_200_OK,
        )


class LearnerActivityFeedView(APIView):
    """
    GET /api/v1/courses/learner/activity/?type=<csv>&page=&page_size=

    Paginated recent-activity feed merged from six sources (lecture
    completions, quiz attempts, assignment and coding submissions,
    enrollments, certificates), newest first.

    The feed is a capped recent window of 200 items, so `count` in the
    envelope is the window size, not lifetime activity. Cost is one indexed
    query per source regardless of page or dataset size.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        try:
            items = get_learner_activity_feed(request.user, request.query_params)
        except ValidationError as e:
            return _validation_error_response(e)
        except Exception:
            logger.exception('Activity feed failed for user=%s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(items, request)
        serializer = LearnerActivityItemSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class LearnerUpcomingView(APIView):
    """
    GET /api/v1/courses/learner/upcoming/?days=<int>&limit=<int>

    Upcoming cohort start/end dates, drip-release section unlocks, and
    registered webinar start times — soonest first.

    Not paginated: the list is inherently small, and paginating an ascending
    union across four sources would need a cursor per source for no benefit.
    Bounded by `days` (default 30, max 365) and `limit` (default 20, max 50).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        try:
            payload = get_learner_upcoming(request.user, request.query_params)
        except ValidationError as e:
            return _validation_error_response(e)
        except Exception:
            logger.exception('Upcoming feed failed for user=%s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                'success': True,
                'message': 'Upcoming items retrieved.',
                'data': LearnerUpcomingSerializer(payload).data,
            },
            status=status.HTTP_200_OK,
        )


class LearnerContinueView(APIView):
    """
    GET /api/v1/courses/learner/continue/

    Resume target — the learner's most recently accessed active enrollment
    plus the first incomplete, unlocked lecture in it.

    Returns 200 with `data: null` when the learner has no active enrollment,
    so the frontend can render the browse-the-catalog empty state without
    special-casing a 404.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        try:
            target = get_continue_target(request.user)
        except Exception:
            logger.exception('Continue target failed for user=%s', request.user.pk)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        data = LearnerContinueSerializer(target).data if target is not None else None
        return Response(
            {'success': True, 'message': 'Continue target retrieved.', 'data': data},
            status=status.HTTP_200_OK,
        )
