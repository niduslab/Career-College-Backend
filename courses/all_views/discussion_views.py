"""
Course Q&A / discussion endpoints.

Routes (all under /api/v1/courses/):
    GET  <slug>/questions/                 → CourseQuestionListView   (enrolled/instructor)
    POST <slug>/questions/                 → CourseQuestionListView   (enrolled/instructor)
    GET  questions/<int:question_id>/      → CourseQuestionDetailView (enrolled/instructor)
    DELETE questions/<int:question_id>/    → CourseQuestionDetailView (author/instructor)
    POST questions/<int:question_id>/replies/ → QuestionReplyCreateView
    POST questions/<int:question_id>/pin/  → QuestionPinView          (instructor)
    POST questions/<int:question_id>/upvote/  → QuestionUpvoteView    (toggle)
    POST replies/<int:reply_id>/upvote/    → ReplyUpvoteView          (toggle)
    DELETE replies/<int:reply_id>/         → QuestionReplyDetailView  (author/instructor)

Access is resolved in the service layer (enrolled learner OR course instructor).
Access-denied status: slug entry → 403, numeric-id entry → 404.
"""

import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified
from courses.all_models.course_models import NidusCourse
from courses.all_serializers.discussion_serializers import (
    CourseQuestionDetailSerializer,
    CourseQuestionListSerializer,
    CourseQuestionWriteSerializer,
    QuestionReplyReadSerializer,
    QuestionReplyWriteSerializer,
)
from courses.services.discussion_service import (
    DiscussionError,
    create_question,
    create_reply,
    delete_question,
    delete_reply,
    get_question_with_replies,
    list_questions,
    toggle_pin,
    upvote_question,
    upvote_reply,
)

logger = logging.getLogger(__name__)


class DiscussionUpvoteThrottle(UserRateThrottle):
    """Per-user rate limit for the counter-only upvote endpoints.

    Upvotes have no per-user vote row (no dedup, no un-upvote) and
    `upvote_count` drives the `?ordering=-upvote_count` sort, so this throttle
    is the only brake on one caller inflating a thread's ranking.
    """

    scope = 'discussion_upvote'
    rate = getattr(settings, 'DISCUSSION_UPVOTE_RATE_LIMIT', '30/min')


def _get_published_course_or_404(slug: str):
    """Fetch a published course by slug or return None (caller sends 404)."""
    try:
        return NidusCourse.objects.get(slug=slug, is_published=True)
    except NidusCourse.DoesNotExist:
        return None


class CourseQuestionListView(APIView):
    """GET — paginated questions for a course. POST — ask a new question."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request, slug):
        course = _get_published_course_or_404(slug)
        if course is None:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            questions = list_questions(request.user, course, request.query_params)
            paginator = StandardResultsSetPagination()
            page = paginator.paginate_queryset(questions, request)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Question list failed for user=%s course=%s', request.user.pk, slug)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = CourseQuestionListSerializer(
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

        serializer = CourseQuestionWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            question = create_question(request.user, course, serializer.validated_data)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Question creation failed for user=%s course=%s', request.user.pk, slug)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Question posted.',
                'data': CourseQuestionListSerializer(question, context={'request': request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CourseQuestionDetailView(APIView):
    """GET — a question with its replies. DELETE — soft-delete (author/instructor)."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request, question_id):
        try:
            question = get_question_with_replies(request.user, question_id)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Question detail failed for user=%s question=%s', request.user.pk, question_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'data': CourseQuestionDetailSerializer(
                    question, context={'request': request}
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, question_id):
        try:
            delete_question(request.user, question_id)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Question deletion failed for user=%s question=%s', request.user.pk, question_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Question deleted.'},
            status=status.HTTP_200_OK,
        )


class QuestionReplyCreateView(APIView):
    """POST — post a reply to a question. Numeric id → 404 on no access."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request, question_id):
        serializer = QuestionReplyWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reply = create_reply(request.user, question_id, serializer.validated_data)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Reply creation failed for user=%s question=%s', request.user.pk, question_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Reply posted.',
                'data': QuestionReplyReadSerializer(reply, context={'request': request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


class QuestionReplyDetailView(APIView):
    """DELETE — soft-delete a reply (author or course instructor)."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def delete(self, request, reply_id):
        try:
            delete_reply(request.user, reply_id)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Reply deletion failed for user=%s reply=%s', request.user.pk, reply_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Reply deleted.'},
            status=status.HTTP_200_OK,
        )


class QuestionPinView(APIView):
    """POST — toggle a question's pinned state. Course instructors only."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def post(self, request, question_id):
        try:
            question = toggle_pin(request.user, question_id)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Question pin toggle failed for user=%s question=%s', request.user.pk, question_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        message = 'Question pinned.' if question.is_pinned else 'Question unpinned.'
        return Response(
            {'success': True, 'message': message, 'data': {'is_pinned': question.is_pinned}},
            status=status.HTTP_200_OK,
        )


class QuestionUpvoteView(APIView):
    """POST — increment a question's upvote counter (counter-only, no toggle)."""

    permission_classes = [IsAuthenticated, IsEmailVerified]
    throttle_classes = [DiscussionUpvoteThrottle]

    def post(self, request, question_id):
        try:
            result = upvote_question(request.user, question_id)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Question upvote failed for user=%s question=%s', request.user.pk, question_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Upvoted.', 'data': result},
            status=status.HTTP_200_OK,
        )


class ReplyUpvoteView(APIView):
    """POST — increment a reply's upvote counter (counter-only, no toggle)."""

    permission_classes = [IsAuthenticated, IsEmailVerified]
    throttle_classes = [DiscussionUpvoteThrottle]

    def post(self, request, reply_id):
        try:
            result = upvote_reply(request.user, reply_id)
        except DiscussionError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception:
            logger.exception('Reply upvote failed for user=%s reply=%s', request.user.pk, reply_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Upvoted.', 'data': result},
            status=status.HTTP_200_OK,
        )
