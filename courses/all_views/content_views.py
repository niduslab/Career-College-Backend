from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsVerifiedInstructor
from courses.models import (
    Assignment,
    CodingExercise,
    CourseSection,
    Lecture,
    Quiz,
    QuizAnswer,
    QuizQuestion,
    SectionContent,
)
from courses.serializers import (
    AssignmentCreateUpdateSerializer,
    CodingExerciseCreateUpdateSerializer,
    CodingExerciseSerializer,
    CourseSectionCreateUpdateSerializer,
    CourseSectionSerializer,
    LectureCreateUpdateSerializer,
    LectureSerializer,
    QuizAnswerSerializer,
    QuizCreateUpdateSerializer,
    QuizQuestionSerializer,
    QuizSerializer,
    SectionContentSerializer,
)
from courses.services import (
    create_section_content_for_object,
    get_course_sections,
    get_next_section_content_position,
    get_section_lectures,
    reorder_section_content,
)
from courses.utils import guard_editable, owned_course_qs, owned_section_qs, save_authored


# =============================================================================
# Section views
# =============================================================================

class CourseSectionListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_course(self, request, course_id):
        return get_object_or_404(owned_course_qs(request.user), pk=course_id)

    def get(self, request, course_id):
        course = self._get_owned_course(request, course_id)
        queryset = get_course_sections(course)
        ordering = request.query_params.get('ordering')
        if ordering in ('position', '-position'):
            queryset = queryset.order_by(ordering, 'id')
        serializer = CourseSectionSerializer(queryset, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)


class CourseSectionCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_course(self, request, course_id):
        return get_object_or_404(owned_course_qs(request.user), pk=course_id)

    def post(self, request, course_id):
        course = self._get_owned_course(request, course_id)
        if err := guard_editable(course): return err
        serializer = CourseSectionCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            section = save_authored(serializer, request.user, course=course)
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A section already exists at this position.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'success': True, 'message': 'Section created successfully.', 'data': CourseSectionSerializer(section).data},
            status=status.HTTP_201_CREATED,
        )


class CourseSectionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_section(self, request, section_id):
        return get_object_or_404(owned_section_qs(request.user), pk=section_id)

    def get(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        return Response({'success': True, 'data': CourseSectionSerializer(section).data}, status=status.HTTP_200_OK)

    def patch(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        if err := guard_editable(section.course, section=section): return err
        serializer = CourseSectionCreateUpdateSerializer(section, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            section = save_authored(serializer, request.user)
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A section already exists at this position.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'success': True, 'message': 'Section updated successfully.', 'data': CourseSectionSerializer(section).data},
            status=status.HTTP_200_OK,
        )

    def put(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        if err := guard_editable(section.course, section=section): return err
        serializer = CourseSectionCreateUpdateSerializer(section, data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            section = save_authored(serializer, request.user)
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A section already exists at this position.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'success': True, 'message': 'Section replaced successfully.', 'data': CourseSectionSerializer(section).data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        if err := guard_editable(section.course, section=section): return err
        section.delete()
        return Response({'success': True, 'message': 'Section deleted successfully.'}, status=status.HTTP_200_OK)


# =============================================================================
# Lecture views — position references removed; SectionContent created on POST
# =============================================================================

class LectureListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_section(self, request, section_id):
        return get_object_or_404(
            CourseSection.objects.select_related('course'),
            pk=section_id,
            course__instructors=request.user,
        )

    def get(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        queryset = get_section_lectures(section)
        serializer = LectureSerializer(queryset, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)


class LectureDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_lecture(self, request, lecture_id):
        queryset = Lecture.objects.select_related('section__course').prefetch_related('video_assets').filter(
            Q(section__course__instructors=request.user) | Q(section__course__created_by=request.user)
        )
        return get_object_or_404(queryset, pk=lecture_id)

    def get(self, request, lecture_id):
        lecture = self._get_owned_lecture(request, lecture_id)
        return Response({'success': True, 'data': LectureSerializer(lecture).data}, status=status.HTTP_200_OK)

    def patch(self, request, lecture_id):
        lecture = self._get_owned_lecture(request, lecture_id)
        if err := guard_editable(lecture.section.course, section=lecture.section): return err
        serializer = LectureCreateUpdateSerializer(
            lecture, data=request.data, partial=True, context={'section': lecture.section}
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            lecture = save_authored(serializer, request.user)
        except ValueError as exc:
            return Response({'success': False, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {'success': True, 'message': 'Lecture updated successfully.', 'data': LectureSerializer(lecture).data},
            status=status.HTTP_200_OK,
        )

    def put(self, request, lecture_id):
        lecture = self._get_owned_lecture(request, lecture_id)
        if err := guard_editable(lecture.section.course, section=lecture.section): return err
        serializer = LectureCreateUpdateSerializer(
            lecture, data=request.data, context={'section': lecture.section}
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            lecture = save_authored(serializer, request.user)
        except ValueError as exc:
            return Response({'success': False, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {'success': True, 'message': 'Lecture replaced successfully.', 'data': LectureSerializer(lecture).data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, lecture_id):
        lecture = self._get_owned_lecture(request, lecture_id)
        if err := guard_editable(lecture.section.course, section=lecture.section): return err
        # GenericRelation on Lecture cascades SectionContent deletion automatically.
        lecture.delete()
        return Response({'success': True, 'message': 'Lecture deleted successfully.'}, status=status.HTTP_200_OK)


# =============================================================================
# SectionContent views
# =============================================================================

class SectionContentListCreateAPIView(APIView):
    """
    GET  /api/sections/{section_id}/contents/  — ordered curriculum list
    POST /api/sections/{section_id}/contents/  — create lecture or quiz + slot
    """
    permission_classes = [IsAuthenticated, IsVerifiedInstructor]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_section(self, request, section_id):
        return get_object_or_404(
            CourseSection.objects.select_related('course'),
            pk=section_id,
            course__instructors=request.user,
        )

    def get(self, request, section_id):
        section = self._get_owned_section(request, section_id)

        contents = list(
            SectionContent.objects
            .filter(section=section)
            .select_related('created_by', 'last_edited_by')
            .order_by('position', 'id')
        )

        # Bulk-load content objects to avoid N+1 queries.
        lecture_ids = [c.object_id for c in contents if c.item_type == SectionContent.ItemType.LECTURE]
        quiz_ids = [c.object_id for c in contents if c.item_type == SectionContent.ItemType.QUIZ]
        coding_ids = [c.object_id for c in contents if c.item_type == SectionContent.ItemType.CODING]
        assignment_ids = [c.object_id for c in contents if c.item_type == SectionContent.ItemType.ASSIGNMENT]

        lectures = (
            {lec.id: lec for lec in Lecture.objects.filter(id__in=lecture_ids)}
            if lecture_ids else {}
        )
        quizzes = (
            {q.id: q for q in Quiz.objects.filter(id__in=quiz_ids)}
            if quiz_ids else {}
        )
        coding_exercises = (
            {ex.id: ex for ex in CodingExercise.objects.filter(id__in=coding_ids)}
            if coding_ids else {}
        )
        assignments = (
            {a.id: a for a in Assignment.objects.filter(id__in=assignment_ids)}
            if assignment_ids else {}
        )

        serializer = SectionContentSerializer(
            contents,
            many=True,
            context={
                'lectures': lectures,
                'quizzes': quizzes,
                'coding_exercises': coding_exercises,
                'assignments': assignments,
            },
        )
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, section_id):
        section = self._get_owned_section(request, section_id)
        if err := guard_editable(section.course): return err
        item_type = request.data.get('item_type', '')

        _VALID_ITEM_TYPES = {
            SectionContent.ItemType.LECTURE,
            SectionContent.ItemType.QUIZ,
            SectionContent.ItemType.CODING,
            SectionContent.ItemType.ASSIGNMENT,
        }
        if item_type not in _VALID_ITEM_TYPES:
            return Response(
                {'success': False, 'message': "item_type must be 'lecture', 'quiz', 'coding', or 'assignment'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        position = _parse_optional_position(request.data.get('position'))
        if position is None and 'position' in request.data:
            return Response(
                {'success': False, 'message': 'position must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if item_type == SectionContent.ItemType.LECTURE:
            return self._create_lecture(request, section, position)
        if item_type == SectionContent.ItemType.QUIZ:
            return self._create_quiz(request, section, position)
        if item_type == SectionContent.ItemType.ASSIGNMENT:
            return self._create_assignment(request, section, position)
        return self._create_coding_exercise(request, section, position)

    def _create_lecture(self, request, section, position):
        # item_type and position are wrapper fields for the /contents/ route,
        # already consumed by post() above. Strip them so the lecture serializer's
        # strict unknown-field check only flags fields actually meant for the lecture.
        data = {
            key: request.data.get(key)
            for key in request.data.keys()
            if key not in {'item_type', 'position'}
        }
        serializer = LectureCreateUpdateSerializer(data=data, context={'section': section})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            with transaction.atomic():
                lecture = save_authored(serializer, request.user)
                sc = create_section_content_for_object(
                    section, lecture, SectionContent.ItemType.LECTURE, position,
                    created_by=request.user,
                )
        except IntegrityError:
            if position is not None and SectionContent.objects.filter(section=section, position=position).exists():
                return Response(
                    {'success': False, 'message': 'A content item already exists at that position.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {'success': False, 'message': 'Could not create lecture due to a data constraint violation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return Response({'success': False, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                'success': True,
                'message': 'Lecture created successfully.',
                'data': SectionContentSerializer(
                    sc, context={'lectures': {lecture.id: lecture}, 'quizzes': {}}
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )

    def _create_quiz(self, request, section, position):
        serializer = QuizCreateUpdateSerializer(data=request.data, context={'section': section})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            with transaction.atomic():
                quiz = save_authored(serializer, request.user)
                sc = create_section_content_for_object(
                    section, quiz, SectionContent.ItemType.QUIZ, position,
                    created_by=request.user,
                )
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A content item already exists at that position.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'success': True,
                'message': 'Quiz created successfully.',
                'data': SectionContentSerializer(
                    sc, context={'lectures': {}, 'quizzes': {quiz.id: quiz}, 'coding_exercises': {}}
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )

    def _create_assignment(self, request, section, position):
        serializer = AssignmentCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            with transaction.atomic():
                assignment = Assignment.objects.create(
                    section=section,
                    created_by=request.user,
                    last_edited_by=request.user,
                    **serializer.validated_data,
                )
                sc = create_section_content_for_object(
                    section, assignment, SectionContent.ItemType.ASSIGNMENT, position,
                    created_by=request.user,
                )
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A content item already exists at that position.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                'success': True,
                'message': 'Assignment created successfully.',
                'data': SectionContentSerializer(
                    sc,
                    context={
                        'lectures': {},
                        'quizzes': {},
                        'coding_exercises': {},
                        'assignments': {assignment.id: assignment},
                    },
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )

    def _create_coding_exercise(self, request, section, position):
        serializer = CodingExerciseCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            with transaction.atomic():
                exercise = CodingExercise.objects.create(
                    section=section,
                    created_by=request.user,
                    last_edited_by=request.user,
                    **serializer.validated_data,
                )
                sc = create_section_content_for_object(
                    section, exercise, SectionContent.ItemType.CODING, position,
                    created_by=request.user,
                )
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A content item already exists at that position.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                'success': True,
                'message': 'Coding exercise created successfully.',
                'data': SectionContentSerializer(
                    sc,
                    context={'lectures': {}, 'quizzes': {}, 'coding_exercises': {exercise.id: exercise}},
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SectionContentReorderAPIView(APIView):
    """
    PATCH /api/contents/{id}/reorder/  — update SectionContent.position only
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_content(self, request, content_id):
        queryset = SectionContent.objects.select_related('section__course').filter(
            Q(section__course__instructors=request.user) | Q(section__course__created_by=request.user)
        )
        return get_object_or_404(queryset, pk=content_id)

    def patch(self, request, content_id):
        sc = self._get_owned_content(request, content_id)
        if err := guard_editable(sc.section.course, section=sc.section): return err

        new_position = request.data.get('position')
        if new_position is None:
            return Response(
                {'success': False, 'message': 'position is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            new_position = int(new_position)
            if new_position < 1:
                raise ValueError
        except (ValueError, TypeError):
            return Response(
                {'success': False, 'message': 'position must be a positive integer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            sc = reorder_section_content(sc, new_position)
        except ValueError as exc:
            return Response({'success': False, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A content item already exists at that position.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {'success': True, 'message': 'Content reordered successfully.', 'data': SectionContentSerializer(sc).data},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Quiz views
# =============================================================================

class QuizDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/quizzes/{id}/"""
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_quiz(self, request, quiz_id):
        return get_object_or_404(
            Quiz.objects.select_related('section__course'),
            pk=quiz_id,
            section__course__instructors=request.user,
        )

    def get(self, request, quiz_id):
        quiz = self._get_owned_quiz(request, quiz_id)
        return Response({'success': True, 'data': QuizSerializer(quiz).data}, status=status.HTTP_200_OK)

    def patch(self, request, quiz_id):
        quiz = self._get_owned_quiz(request, quiz_id)
        if err := guard_editable(quiz.section.course, section=quiz.section): return err
        serializer = QuizCreateUpdateSerializer(quiz, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        quiz = save_authored(serializer, request.user)
        return Response(
            {'success': True, 'message': 'Quiz updated successfully.', 'data': QuizSerializer(quiz).data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, quiz_id):
        quiz = self._get_owned_quiz(request, quiz_id)
        if err := guard_editable(quiz.section.course, section=quiz.section): return err
        # GenericRelation on Quiz cascades SectionContent deletion automatically.
        with transaction.atomic():
            quiz.delete()
        return Response({'success': True, 'message': 'Quiz deleted successfully.'}, status=status.HTTP_200_OK)


# =============================================================================
# QuizQuestion views
# =============================================================================

class QuizQuestionListCreateAPIView(APIView):
    """GET / POST /api/quizzes/{quiz_id}/questions/"""
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_quiz(self, request, quiz_id):
        return get_object_or_404(
            Quiz.objects.select_related('section__course'),
            pk=quiz_id,
            section__course__instructors=request.user,
        )

    def get(self, request, quiz_id):
        quiz = self._get_owned_quiz(request, quiz_id)
        questions = quiz.questions.order_by('position', 'id')
        serializer = QuizQuestionSerializer(questions, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, quiz_id):
        quiz = self._get_owned_quiz(request, quiz_id)
        if err := guard_editable(quiz.section.course): return err
        serializer = QuizQuestionSerializer(data=request.data, context={'quiz': quiz})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            question = serializer.save()
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A question already exists at that position in this quiz.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'success': True, 'message': 'Question created successfully.', 'data': QuizQuestionSerializer(question).data},
            status=status.HTTP_201_CREATED,
        )


class QuizQuestionDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/quiz-questions/{id}/"""
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_question(self, request, question_id):
        return get_object_or_404(
            QuizQuestion.objects.select_related('quiz__section__course'),
            pk=question_id,
            quiz__section__course__instructors=request.user,
        )

    def get(self, request, question_id):
        question = self._get_owned_question(request, question_id)
        return Response(
            {'success': True, 'data': QuizQuestionSerializer(question).data}, status=status.HTTP_200_OK
        )

    def patch(self, request, question_id):
        question = self._get_owned_question(request, question_id)
        if err := guard_editable(question.quiz.section.course, section=question.quiz.section): return err
        serializer = QuizQuestionSerializer(question, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            question = serializer.save()
        except IntegrityError:
            return Response(
                {'success': False, 'message': 'A question already exists at that position in this quiz.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'success': True, 'message': 'Question updated successfully.', 'data': QuizQuestionSerializer(question).data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, question_id):
        question = self._get_owned_question(request, question_id)
        if err := guard_editable(question.quiz.section.course, section=question.quiz.section): return err
        question.delete()
        return Response(
            {'success': True, 'message': 'Question deleted successfully.'}, status=status.HTTP_200_OK
        )


# =============================================================================
# QuizAnswer views
# =============================================================================

class QuizAnswerListCreateAPIView(APIView):
    """GET / POST /api/quiz-questions/{question_id}/answers/"""
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_question(self, request, question_id):
        return get_object_or_404(
            QuizQuestion.objects.select_related('quiz__section__course'),
            pk=question_id,
            quiz__section__course__instructors=request.user,
        )

    def get(self, request, question_id):
        question = self._get_owned_question(request, question_id)
        answers = question.answers.order_by('id')
        serializer = QuizAnswerSerializer(answers, many=True)
        return Response({'success': True, 'data': serializer.data}, status=status.HTTP_200_OK)

    def post(self, request, question_id):
        question = self._get_owned_question(request, question_id)
        if err := guard_editable(question.quiz.section.course): return err
        serializer = QuizAnswerSerializer(data=request.data, context={'question': question})
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        answer = serializer.save()
        return Response(
            {'success': True, 'message': 'Answer created successfully.', 'data': QuizAnswerSerializer(answer).data},
            status=status.HTTP_201_CREATED,
        )


class QuizAnswerDetailAPIView(APIView):
    """GET / PATCH / DELETE /api/quiz-answers/{id}/"""
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def _get_owned_answer(self, request, answer_id):
        queryset = QuizAnswer.objects.select_related('question__quiz__section__course').filter(
            Q(question__quiz__section__course__instructors=request.user)
            | Q(question__quiz__section__course__created_by=request.user)
        )
        return get_object_or_404(queryset, pk=answer_id)

    def get(self, request, answer_id):
        answer = self._get_owned_answer(request, answer_id)
        return Response({'success': True, 'data': QuizAnswerSerializer(answer).data}, status=status.HTTP_200_OK)

    def patch(self, request, answer_id):
        answer = self._get_owned_answer(request, answer_id)
        if err := guard_editable(answer.question.quiz.section.course, section=answer.question.quiz.section): return err
        serializer = QuizAnswerSerializer(answer, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        answer = serializer.save()
        return Response(
            {'success': True, 'message': 'Answer updated successfully.', 'data': QuizAnswerSerializer(answer).data},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, answer_id):
        answer = self._get_owned_answer(request, answer_id)
        if err := guard_editable(answer.question.quiz.section.course, section=answer.question.quiz.section): return err
        answer.delete()
        return Response(
            {'success': True, 'message': 'Answer deleted successfully.'}, status=status.HTTP_200_OK
        )


# =============================================================================
# Shared helpers
# =============================================================================

def _parse_optional_position(raw) -> int | None:
    """Return a validated positive integer or None. None means 'not provided'."""
    if raw is None:
        return None
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except (ValueError, TypeError):
        return None
