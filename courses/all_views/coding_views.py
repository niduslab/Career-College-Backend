import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsCourseCreator, IsEmailVerified
from courses.models import CodingExercise
from courses.serializers import (
    CodingExerciseCreateUpdateSerializer,
    CodingExerciseSerializer,
)
from courses.services.code_runner import SMOKE_EVALUATION_SCRIPTS
from courses.utils import course_owner_q, guard_editable, save_authored

logger = logging.getLogger(__name__)


def _owned_exercise_qs(user):
    return (
        CodingExercise.objects
        .select_related('section__course')
        .filter(course_owner_q(user, 'section__course'))
        .distinct()
    )


class CodingExerciseDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/courses/coding-exercises/{exercise_id}/"""
    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_exercise(self, request, exercise_id):
        return get_object_or_404(_owned_exercise_qs(request.user), pk=exercise_id)

    def get(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        return Response(
            {'success': True, 'data': CodingExerciseSerializer(exercise).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        if err := guard_editable(exercise.section.course, section=exercise.section): return err
        serializer = CodingExerciseCreateUpdateSerializer(exercise, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            exercise = save_authored(serializer, request.user)
        except Exception:
            logger.exception('Coding exercise update failed for user %s', request.user.id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                'success': True,
                'message': 'Coding exercise updated successfully.',
                'data': CodingExerciseSerializer(exercise).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        if err := guard_editable(exercise.section.course, section=exercise.section): return err
        # GenericRelation on CodingExercise cascades SectionContent deletion automatically.
        exercise.delete()
        return Response(
            {'success': True, 'message': 'Coding exercise deleted successfully.'},
            status=status.HTTP_200_OK,
        )

class CodingExerciseRunAPIView(APIView):
    """POST /api/courses/coding-exercises/{exercise_id}/run/

    Instructor-side transient run so an exercise can be tested while
    authoring. Body (all optional):
      - code: source to execute (defaults to the stored solution_code)
      - evaluation_script: test script to run it against (defaults to the
        stored evaluation_script) — lets the instructor test unsaved edits
      - mode: 'tests' (default) runs the evaluation script; 'code' runs the
        code standalone via a synthetic one-test smoke script (top-level
        output captured; compile check for java/cpp)

    Returns 202 + {task_id}; poll GET /learn/coding-exercises/tasks/{task_id}/
    (that endpoint is IsEmailVerified-gated, not learner-only).
    """
    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, exercise_id):
        exercise = get_object_or_404(_owned_exercise_qs(request.user), pk=exercise_id)

        mode = request.data.get('mode') or 'tests'
        if mode not in ('tests', 'code'):
            return Response(
                {'success': False, 'message': "mode must be 'tests' or 'code'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = request.data.get('code')
        if code is None:
            code = exercise.solution_code
        if not isinstance(code, str) or not code.strip():
            return Response(
                {'success': False, 'message': 'No code to run — write a solution first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if mode == 'code':
            evaluation_script = SMOKE_EVALUATION_SCRIPTS[exercise.language]
        else:
            evaluation_script = request.data.get('evaluation_script')
            if evaluation_script is None:
                evaluation_script = exercise.evaluation_script
            if not isinstance(evaluation_script, str) or not evaluation_script.strip():
                return Response(
                    {'success': False, 'message': 'No evaluation script to run — write one first.'},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

        from courses.tasks import evaluate_coding_run_task  # local: avoid Celery at module load
        try:
            async_result = evaluate_coding_run_task.delay(
                exercise.id,
                exercise.language,
                code,
                exercise.time_limit_ms,
                evaluation_script,
            )
        except Exception:
            logger.exception('Instructor run dispatch failed for exercise %s', exercise_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Run dispatched.', 'data': {'task_id': async_result.id}},
            status=status.HTTP_202_ACCEPTED,
        )
