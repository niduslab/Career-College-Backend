import logging

from django.db import IntegrityError
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsCourseCreator, IsEmailVerified
from courses.models import Assignment, AssignmentQuestion
from courses.serializers import (
    AssignmentCreateUpdateSerializer,
    AssignmentQuestionSerializer,
    AssignmentSerializer,
)
from courses.services import (
    add_question,
    delete_assignment,
    delete_question,
    reorder_questions,
    update_assignment,
    update_question,
)
from courses.services.rubric_autogen import (
    DEFAULT_MAX_TERMS,
    generate_rubric_from_model_answer,
)
from courses.utils import guard_editable, owned_section_qs

logger = logging.getLogger(__name__)


def _owned_section_ids(user):
    """Section pks the user owns, as a subquery.

    Filtering with `section__in=<subquery>` instead of joining through
    `section__course__instructors` keeps the `Sum('questions__points')`
    annotation on the assignment querysets correct — an ownership join on a
    multi-valued relation would multiply the aggregate.
    """
    return owned_section_qs(user).values('pk')


class StrictIntegerField(serializers.IntegerField):
    """Reject bools/floats/strings; accept only true integer JSON values."""
    def to_internal_value(self, data):
        if type(data) is not int:
            raise serializers.ValidationError('A valid integer is required.')
        return super().to_internal_value(data)


class AssignmentQuestionReorderInputSerializer(serializers.Serializer):
    ordered_ids = serializers.ListField(
        child=StrictIntegerField(),
        allow_empty=False,
    )


# =============================================================================
# Assignment list — scoped to a section
# =============================================================================

class AssignmentListAPIView(APIView):
    """GET /api/v1/courses/sections/{section_id}/assignments/"""
    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_section(self, request, section_id):
        return get_object_or_404(owned_section_qs(request.user), pk=section_id)

    def get(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        assignments = (
            Assignment.objects
            .filter(section=section)
            .annotate(max_score=Coalesce(Sum('questions__points'), Value(0)))
            .prefetch_related('questions')
            .order_by('-created_at')
        )
        serializer = AssignmentSerializer(assignments, many=True, context={'request': request})
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)


# =============================================================================
# Assignment detail
# =============================================================================

class AssignmentDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/v1/courses/assignments/{assignment_id}/"""
    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_permissions(self):
        if self.request.method in ('PATCH', 'PUT', 'DELETE'):
            return [IsAuthenticated(), IsEmailVerified(), IsCourseCreator()]
        return super().get_permissions()

    def _get_owned_assignment(self, request, assignment_id):
        return get_object_or_404(
            Assignment.objects
            .annotate(max_score=Coalesce(Sum('questions__points'), Value(0)))
            .select_related('section__course')
            .prefetch_related('questions')
            .filter(section__in=_owned_section_ids(request.user)),
            pk=assignment_id,
        )

    def get(self, request, assignment_id):
        assignment = self._get_owned_assignment(request, assignment_id)
        return Response(
            {'success': True, 'data': AssignmentSerializer(assignment, context={'request': request}).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, assignment_id):
        # Confirm ownership before delegating to the service.
        assignment = self._get_owned_assignment(request, assignment_id)
        if err := guard_editable(assignment.section.course, section=assignment.section):
            return err

        # Pass the instance so cross-field validation (e.g. passing_score
        # <= total_score) can fall back to existing values when one side is
        # absent from the partial payload.
        serializer = AssignmentCreateUpdateSerializer(
            instance=assignment, data=request.data, partial=True,
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            assignment = update_assignment(assignment_id, request.user, serializer.validated_data)
        except Exception:
            logger.exception('Assignment update failed for user %s', request.user.id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Assignment updated successfully.',
                'data': AssignmentSerializer(assignment, context={'request': request}).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, assignment_id):
        # Confirm ownership before delegating.
        assignment = self._get_owned_assignment(request, assignment_id)
        if err := guard_editable(assignment.section.course, section=assignment.section):
            return err
        try:
            delete_assignment(assignment_id, request.user)
        except Exception:
            logger.exception('Assignment delete failed for user %s', request.user.id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Assignment deleted successfully.'},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Assignment question list/create
# =============================================================================

class AssignmentQuestionListCreateAPIView(APIView):
    """GET / POST /api/v1/courses/assignments/{assignment_id}/questions/"""
    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), IsEmailVerified(), IsCourseCreator()]
        return super().get_permissions()

    def _get_owned_assignment(self, request, assignment_id):
        return get_object_or_404(
            Assignment.objects
            .select_related('section__course')
            .filter(section__in=_owned_section_ids(request.user)),
            pk=assignment_id,
        )

    def get(self, request, assignment_id):
        assignment = self._get_owned_assignment(request, assignment_id)
        questions = assignment.questions.order_by('position', 'id')
        serializer = AssignmentQuestionSerializer(questions, many=True, context={'request': request})
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, assignment_id):
        # Confirm ownership before delegating.
        assignment = self._get_owned_assignment(request, assignment_id)
        if err := guard_editable(assignment.section.course):
            return err

        serializer = AssignmentQuestionSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Filter to fields the service actually owns; position is assigned by the service.
        write_fields = {'question_text', 'model_answer', 'rubric', 'points', 'hint'}
        payload = {k: v for k, v in serializer.validated_data.items() if k in write_fields}

        try:
            question = add_question(assignment_id, request.user, payload)
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A question already exists at that position in this assignment.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception('Assignment question create failed for user %s', request.user.id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Question created successfully.',
                'data': AssignmentQuestionSerializer(question, context={'request': request}).data,
            },
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# Assignment question detail
# =============================================================================

class AssignmentQuestionDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/v1/courses/assignment-questions/{question_id}/"""
    permission_classes = [IsAuthenticated, IsEmailVerified]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get_permissions(self):
        if self.request.method in ('PATCH', 'PUT', 'DELETE'):
            return [IsAuthenticated(), IsEmailVerified(), IsCourseCreator()]
        return super().get_permissions()

    def _get_owned_question(self, request, question_id):
        return get_object_or_404(
            AssignmentQuestion.objects
            .select_related('assignment__section__course')
            .filter(assignment__section__in=_owned_section_ids(request.user)),
            pk=question_id,
        )

    def get(self, request, question_id):
        question = self._get_owned_question(request, question_id)
        return Response(
            {'success': True, 'data': AssignmentQuestionSerializer(question, context={'request': request}).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request, question_id):
        question = self._get_owned_question(request, question_id)
        if err := guard_editable(question.assignment.section.course, section=question.assignment.section):
            return err

        # Pass the instance so the rubric / points cross-field validator can
        # fall back to existing values when one side is absent from the
        # partial payload.
        serializer = AssignmentQuestionSerializer(
            instance=question, data=request.data, partial=True,
            context={'request': request},
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        write_fields = {'question_text', 'model_answer', 'rubric', 'points', 'hint'}
        payload = {k: v for k, v in serializer.validated_data.items() if k in write_fields}

        try:
            question = update_question(question_id, request.user, payload)
        except Exception:
            logger.exception('Assignment question update failed for user %s', request.user.id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                'success': True,
                'message': 'Question updated successfully.',
                'data': AssignmentQuestionSerializer(question, context={'request': request}).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, question_id):
        question = self._get_owned_question(request, question_id)
        if err := guard_editable(question.assignment.section.course, section=question.assignment.section):
            return err
        try:
            delete_question(question_id, request.user)
        except Exception:
            logger.exception('Assignment question delete failed for user %s', request.user.id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'success': True, 'message': 'Question deleted successfully.'},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Rubric auto-generation preview
# =============================================================================

class RubricPreviewInputSerializer(serializers.Serializer):
    """Body for the rubric-preview endpoint. `points` mirrors the question's
    integer points; `max_terms` is optional and caps how many keyword criteria
    are produced."""
    model_answer = serializers.CharField(allow_blank=True, trim_whitespace=False)
    points = StrictIntegerField(min_value=0)
    max_terms = StrictIntegerField(min_value=1, required=False, default=DEFAULT_MAX_TERMS)


class AssignmentRubricPreviewAPIView(APIView):
    """POST /api/v1/courses/assignments/rubric-preview/

    Stateless helper: given a model answer + points, return the rubric that
    Option B auto-generation would produce. The authoring UI calls this so an
    instructor can SEE (and then edit) the generated criteria before saving,
    rather than discovering them only after the question is persisted. Does not
    touch the database and is not tied to a specific question."""
    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        serializer = RubricPreviewInputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        # split_points=False: return criteria with 0 points so the instructor
        # assigns points manually per criterion in the UI.
        rubric = generate_rubric_from_model_answer(
            data['model_answer'], data['points'], max_terms=data['max_terms'],
            split_points=False,
        )
        return Response(
            {'success': True, 'data': {'rubric': rubric}},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Assignment question reorder
# =============================================================================

class AssignmentQuestionReorderAPIView(APIView):
    """PATCH /api/v1/courses/assignments/{assignment_id}/questions/reorder/"""
    permission_classes = [IsAuthenticated, IsEmailVerified, IsCourseCreator]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def patch(self, request, assignment_id):
        input_serializer = AssignmentQuestionReorderInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(
                {'success': False, 'message': 'ordered_ids must be a non-empty list of integers only.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ordered_ids = input_serializer.validated_data['ordered_ids']

        assignment = get_object_or_404(
            Assignment.objects
            .select_related('section__course')
            .filter(section__in=_owned_section_ids(request.user)),
            pk=assignment_id,
        )
        if err := guard_editable(assignment.section.course, section=assignment.section):
            return err

        try:
            questions = reorder_questions(assignment_id, request.user, ordered_ids)
        except Http404:
            return Response(
                {'success': False, 'message': 'Assignment not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValueError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'Reorder failed due to a position conflict.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception('Assignment question reorder failed for user %s', request.user.id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = AssignmentQuestionSerializer(questions, many=True, context={'request': request})
        return Response(
            {'success': True, 'message': 'Questions reordered successfully.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )
