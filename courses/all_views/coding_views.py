import logging

from django.db import IntegrityError, transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified, IsVerifiedInstructor
from courses.models import (
    CodingExercise,
    CodingExerciseLanguageConfig,
    CodingTestCase,
)
from courses.serializers import (
    CodingExerciseCreateUpdateSerializer,
    CodingExerciseLanguageConfigSerializer,
    CodingExerciseSerializer,
    CodingTestCaseSerializer,
)
from courses.utils import guard_editable, save_authored

logger = logging.getLogger(__name__)


class CodingExerciseDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/courses/coding-exercises/{exercise_id}/"""
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_exercise(self, request, exercise_id):
        return get_object_or_404(
            CodingExercise.objects
            .select_related('section__course')
            .prefetch_related('language_configs', 'test_cases'),
            pk=exercise_id,
            section__course__instructors=request.user,
        )

    def get(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        return Response(
            {'success': True, 'data': CodingExerciseSerializer(exercise).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        if err := guard_editable(exercise.section.course): return err
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
        if err := guard_editable(exercise.section.course): return err
        # GenericRelation on CodingExercise cascades SectionContent deletion automatically.
        exercise.delete()
        return Response(
            {'success': True, 'message': 'Coding exercise deleted successfully.'},
            status=status.HTTP_200_OK,
        )


class CodingExerciseLanguageConfigListCreateAPIView(APIView):
    """GET / POST /api/courses/coding-exercises/{exercise_id}/language-configs/"""
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_exercise(self, request, exercise_id):
        return get_object_or_404(
            CodingExercise.objects.select_related('section__course'),
            pk=exercise_id,
            section__course__instructors=request.user,
        )

    def get(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        configs = exercise.language_configs.all()
        serializer = CodingExerciseLanguageConfigSerializer(configs, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        if err := guard_editable(exercise.section.course): return err
        serializer = CodingExerciseLanguageConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            config = CodingExerciseLanguageConfig.objects.create(
                exercise=exercise, **serializer.validated_data
            )
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A config for this language already exists on this exercise.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                'success': True,
                'message': 'Language config created successfully.',
                'data': CodingExerciseLanguageConfigSerializer(config).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CodingExerciseLanguageConfigDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/courses/coding-exercises/{exercise_id}/language-configs/{config_id}/"""
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_config(self, request, exercise_id, config_id):
        return get_object_or_404(
            CodingExerciseLanguageConfig.objects.select_related('exercise__section__course'),
            pk=config_id,
            exercise_id=exercise_id,
            exercise__section__course__instructors=request.user,
        )

    def get(self, request, exercise_id, config_id):
        config = self._get_owned_config(request, exercise_id, config_id)
        return Response(
            {'success': True, 'data': CodingExerciseLanguageConfigSerializer(config).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, exercise_id, config_id):
        config = self._get_owned_config(request, exercise_id, config_id)
        if err := guard_editable(config.exercise.section.course): return err
        serializer = CodingExerciseLanguageConfigSerializer(config, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            config = serializer.save()
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A config for this language already exists on this exercise.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                'success': True,
                'message': 'Language config updated successfully.',
                'data': CodingExerciseLanguageConfigSerializer(config).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, exercise_id, config_id):
        config = self._get_owned_config(request, exercise_id, config_id)
        if err := guard_editable(config.exercise.section.course): return err
        config.delete()
        return Response(
            {'success': True, 'message': 'Language config deleted successfully.'},
            status=status.HTTP_200_OK,
        )


class CodingTestCaseListCreateAPIView(APIView):
    """GET / POST /api/courses/coding-exercises/{exercise_id}/testcases/"""
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_exercise(self, request, exercise_id):
        return get_object_or_404(
            CodingExercise.objects.select_related('section__course'),
            pk=exercise_id,
            section__course__instructors=request.user,
        )

    def get(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        test_cases = exercise.test_cases.order_by('position', 'id')
        serializer = CodingTestCaseSerializer(test_cases, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, exercise_id):
        exercise = self._get_owned_exercise(request, exercise_id)
        if err := guard_editable(exercise.section.course): return err
        serializer = CodingTestCaseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            test_case = CodingTestCase.objects.create(exercise=exercise, **serializer.validated_data)
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A test case already exists at that position for this exercise.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                'success': True,
                'message': 'Test case created successfully.',
                'data': CodingTestCaseSerializer(test_case).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CodingTestCaseDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/courses/coding-exercises/{exercise_id}/testcases/{tc_id}/"""
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_test_case(self, request, exercise_id, tc_id):
        return get_object_or_404(
            CodingTestCase.objects.select_related('exercise__section__course'),
            pk=tc_id,
            exercise_id=exercise_id,
            exercise__section__course__instructors=request.user,
        )

    def get(self, request, exercise_id, tc_id):
        test_case = self._get_owned_test_case(request, exercise_id, tc_id)
        return Response(
            {'success': True, 'data': CodingTestCaseSerializer(test_case).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, exercise_id, tc_id):
        test_case = self._get_owned_test_case(request, exercise_id, tc_id)
        if err := guard_editable(test_case.exercise.section.course): return err
        serializer = CodingTestCaseSerializer(test_case, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            test_case = serializer.save()
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A test case already exists at that position for this exercise.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                'success': True,
                'message': 'Test case updated successfully.',
                'data': CodingTestCaseSerializer(test_case).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, exercise_id, tc_id):
        test_case = self._get_owned_test_case(request, exercise_id, tc_id)
        if err := guard_editable(test_case.exercise.section.course): return err
        with transaction.atomic():
            deleted_position = test_case.position
            owned_exercise_id = test_case.exercise_id
            test_case.delete()
            # Keep positions contiguous after deletion: 1,2,3... with no gaps.
            CodingTestCase.objects.filter(
                exercise_id=owned_exercise_id,
                position__gt=deleted_position,
            ).update(position=F('position') - 1)
        return Response(
            {'success': True, 'message': 'Test case deleted successfully.'},
            status=status.HTTP_200_OK,
        )
