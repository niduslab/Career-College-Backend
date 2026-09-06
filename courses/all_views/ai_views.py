"""AI-assisted authoring endpoints.

Routes (under /api/v1/courses/):
    POST ai/outline-preview/          → CourseOutlinePreviewAPIView
    POST ai/article-lecture-preview/  → ArticleLecturePreviewAPIView
    POST ai/quiz-questions-preview/   → QuizQuestionsPreviewAPIView
    POST ai/coding-exercise-preview/  → CodingExercisePreviewAPIView
All gated for instructors and partner institutions.

Every endpoint here is a **suggestion generator**: it calls the AI services
project, returns the result, and persists nothing. The human decides what to
keep — same rule as the rubric preview (`AssignmentRubricPreviewAPIView`).

Denial status follows the URL, not the module (CLAUDE.md → 403 vs. 404
Access-Denied Policy). The first two take no resource id, so denial is 403.
The quiz and coding endpoints take a resource id in the body and scope it by
ownership like every other view on that resource, so one the caller does not own
is a 404 — they read the database for grounding material, and 403 there would
confirm the row exists. Reading for context is still not persisting: none write.
"""

import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from core.permissions import IsCourseCreator, IsEmailVerified
from courses.all_serializers.ai_serializers import (
    ArticleLectureRequestSerializer,
    CodingExerciseRequestSerializer,
    CourseOutlineRequestSerializer,
    QuizQuestionsRequestSerializer,
)
from courses.all_views.coding_views import _owned_exercise_qs
from courses.models import Quiz
from courses.services.ai_article_service import AIArticleError, generate_article_lecture
from courses.services.ai_outline_service import AIOutlineError, generate_course_outline
from courses.services.ai_coding_service import AICodingError, generate_coding_exercise
from courses.services.ai_quiz_service import AIQuizError, generate_quiz_questions
from courses.services.quiz_service import (
    build_quiz_source_material,
    collect_avoid_questions,
)
from courses.services.section_context_service import build_section_source_material
from courses.utils import course_owner_q

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


class AIQuizThrottle(UserRateThrottle):
    """Per-user rate limit for quiz-question generation.

    Its own scope: questions are generated per quiz, outlines once per course,
    articles once per lesson, so a shared counter would let one exhaust the rest.
    """

    scope = 'ai_quiz'
    rate = getattr(settings, 'AI_QUIZ_RATE_LIMIT', '10/min')


class AICodingThrottle(UserRateThrottle):
    """Per-user rate limit for coding-exercise generation.

    Its own scope, fourth in the family: an exercise is generated once per
    exercise, so a shared counter would let one feature exhaust the others.
    """

    scope = 'ai_coding'
    rate = getattr(settings, 'AI_CODING_RATE_LIMIT', '10/min')


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


class QuizQuestionsPreviewAPIView(APIView):
    """POST /api/v1/courses/ai/quiz-questions-preview/

    Draft multiple-choice questions for one quiz, grounded in the lectures
    beside it. Accepting posts to `quizzes/<id>/questions/bulk/`, which writes.

    **Reads the database, writes nothing.** Never make it write: a generated
    question nobody read is exactly what `_validate_course_completeness` cannot
    catch, because it is complete — just possibly wrong.

    The `quiz_id` and the 404 it produces are both deliberate; see
    `QuizQuestionsRequestSerializer`.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    throttle_classes = [AIQuizThrottle]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        serializer = QuizQuestionsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        # Scoped like every other quiz view: not-yours is indistinguishable
        # from does-not-exist.
        queryset = (
            Quiz.objects
            .select_related('section__course')
            .filter(course_owner_q(request.user, 'section__course'))
            .distinct()
        )
        quiz = get_object_or_404(queryset, pk=data['quiz_id'])
        course = quiz.section.course

        source_material, grounded = build_quiz_source_material(quiz)

        try:
            result = generate_quiz_questions(
                quiz_title=quiz.title,
                course_title=course.title,
                section_title=quiz.section.title,
                quiz_description=quiz.description,
                source_material=source_material,
                topics=data['topics'],
                audience=course.audiences,
                level=course.level,
                language=course.language,
                question_count=data['question_count'],
                options_per_question=data['options_per_question'],
                difficulty=data['difficulty'],
                avoid_questions=collect_avoid_questions(quiz, data['avoid_questions']),
                extra_instructions=data['extra_instructions'],
            )
        except AIQuizError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception as e:
            logger.error(
                'Quiz question generation failed for user %s: %s', request.user.id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # `grounded` is decided here: the AI service only knows whether it was
        # given material, not whether the section actually has any.
        return Response(
            {
                'success': True,
                'message': 'Questions generated.',
                'data': {**result, 'grounded': grounded},
            },
            status=status.HTTP_200_OK,
        )


class CodingExercisePreviewAPIView(APIView):
    """POST /api/v1/courses/ai/coding-exercise-preview/

    Draft one coding exercise — description, starter code, reference solution
    and evaluation script — grounded in the module's lectures.

    **Reads the database, writes nothing, and runs nothing.** The caller proves
    the exercise works by running the solution and the starter code through
    `POST coding-exercises/<id>/run/`, which already accepts both as overrides,
    and only then does the instructor accept it. That verification matters more
    here than on the other AI endpoints: a broken evaluation script is
    non-empty, so it passes `_validate_course_completeness` and fails for the
    first learner instead.

    `language` comes from the stored exercise, never the request — it decides
    which evaluation-script contract the generated script must satisfy.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    throttle_classes = [AICodingThrottle]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        serializer = CodingExerciseRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        exercise = get_object_or_404(
            _owned_exercise_qs(request.user), pk=data['exercise_id'],
        )
        course = exercise.section.course

        source_material, grounded = build_section_source_material(exercise.section)

        try:
            result = generate_coding_exercise(
                exercise_title=exercise.title,
                language=exercise.language,
                course_title=course.title,
                section_title=exercise.section.title,
                exercise_description=exercise.description,
                source_material=source_material,
                audience=course.audiences,
                level=course.level,
                natural_language=course.language,
                difficulty=data['difficulty'],
                topic_hint=data['topic_hint'],
                time_limit_ms=exercise.time_limit_ms,
                avoid_titles=data['avoid_titles'],
                extra_instructions=data['extra_instructions'],
            )
        except AICodingError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )
        except Exception as e:
            logger.error(
                'Coding exercise generation failed for user %s: %s', request.user.id, e,
            )
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Exercise generated.',
                'data': {**result, 'language': exercise.language, 'grounded': grounded},
            },
            status=status.HTTP_200_OK,
        )
