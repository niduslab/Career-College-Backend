"""AI-assisted authoring endpoints.

Routes (under /api/v1/courses/):
    POST ai/outline-preview/         → CourseOutlinePreviewAPIView
    POST ai/article-lecture-preview/ → ArticleLecturePreviewAPIView
Both gated for instructors and partner institutions.

Every endpoint here is a **suggestion generator**: it calls the AI services
project, returns the result, and persists nothing. The human decides what to
keep — same rule as the rubric preview (`AssignmentRubricPreviewAPIView`).

No resource id appears in these URLs, so permission denial is always 403,
never 404 (see CLAUDE.md → 403 vs. 404 Access-Denied Policy).
"""

import logging

from django.conf import settings
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from core.permissions import IsCourseCreator, IsEmailVerified
from courses.all_serializers.ai_serializers import (
    ArticleLectureRequestSerializer,
    CourseOutlineRequestSerializer,
)
from courses.services.ai_article_service import AIArticleError, generate_article_lecture
from courses.services.ai_outline_service import AIOutlineError, generate_course_outline

logger = logging.getLogger(__name__)


class AIOutlineThrottle(UserRateThrottle):
    """Per-user rate limit for outline generation.

    Unlike every other authoring endpoint, each call here costs real money
    (LLM usage) and takes several seconds. The throttle is the spend brake —
    without it, a held-down button bills the platform for nothing.
    """

    scope = 'ai_outline'
    rate = getattr(settings, 'AI_OUTLINE_RATE_LIMIT', '10/min')


class AIArticleThrottle(UserRateThrottle):
    """Per-user rate limit for article-lecture generation.

    Its own scope, not shared with the outline throttle: the two are used at
    different points in the build (once per course vs. once per lesson), so one
    counter would let outlining exhaust a writing session's budget.
    """

    scope = 'ai_article'
    rate = getattr(settings, 'AI_ARTICLE_RATE_LIMIT', '10/min')


class CourseOutlinePreviewAPIView(APIView):
    """POST /api/v1/courses/ai/outline-preview/

    Generate a course-outline suggestion from course metadata. Stateless: the
    caller edits the result and saves it through the normal course endpoints —
    `course_outline` on POST/PATCH `/courses/`, or one `CourseSection` per
    module via the existing section-create endpoint. Nothing is written here.

    Gated with `IsCourseCreator` (not the verified variant) so it matches
    `CourseCreateAPIView`: authoring must work before identity verification
    completes, and it must cover partner institutions as well as instructors.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    throttle_classes = [AIOutlineThrottle]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        serializer = CourseOutlineRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = generate_course_outline(**serializer.validated_data)
        except AIOutlineError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception as e:
            logger.error(
                'Outline generation failed for user %s: %s', request.user.id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Outline generated.', 'data': result},
            status=status.HTTP_200_OK,
        )


class ArticleLecturePreviewAPIView(APIView):
    """POST /api/v1/courses/ai/article-lecture-preview/

    Draft the body of one **article** lecture from its title and the context
    around it. Returns `article_html` ready for the builder's rich-text editor,
    plus the structure it was rendered from and a word/reading-time count.

    Stateless: nothing is written here. The instructor edits the draft in the
    editor and saves it through the lecture endpoint that already exists —
    `PATCH /api/v1/courses/lectures/<id>/` with `lecture_type='article'` and
    `article_content`. **Never make this endpoint write**: an AI body saved
    without a human reading it would satisfy `chk_lecture_payload_by_type` and
    sail through submission validation, which is exactly the check that stops a
    hollow lecture reaching learners.

    Video lectures are out of scope by construction — they need a real uploaded
    file that must finish transcoding.

    Gated with `IsCourseCreator` (not the verified variant) so it matches the
    lecture-authoring endpoints: authoring must work before identity
    verification completes, and it must cover partner institutions as well as
    instructors.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    throttle_classes = [AIArticleThrottle]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        serializer = ArticleLectureRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = generate_article_lecture(**serializer.validated_data)
        except AIArticleError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception as e:
            logger.error(
                'Article generation failed for user %s: %s', request.user.id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Article generated.', 'data': result},
            status=status.HTTP_200_OK,
        )
