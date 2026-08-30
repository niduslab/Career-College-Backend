"""AI-assisted authoring endpoints.

Routes (under /api/v1/courses/):
    POST ai/outline-preview/  → CourseOutlinePreviewAPIView  (instructor/institution)

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
from courses.all_serializers.ai_serializers import CourseOutlineRequestSerializer
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
