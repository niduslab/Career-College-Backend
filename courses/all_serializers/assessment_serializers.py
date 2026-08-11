import re

from django.db import transaction
from rest_framework import serializers

from courses.all_serializers.course_serializers import InstructorBriefSerializer
from courses.models import (
    Assignment,
    AssignmentQuestion,
    CodingExercise,
    CourseSection,
    Quiz,
    QuizAnswer,
    QuizQuestion,
)


# additive: add an entry here AND a matcher in RubricGrader.
_RUBRIC_CRITERION_VALUE_VALIDATORS = {
    'keyword': lambda v: isinstance(v, str) and bool(v.strip()),
    'regex': lambda v: isinstance(v, str) and bool(v.strip()),
    'min_length': lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    'max_length': lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    'any_of': lambda v: isinstance(v, list) and bool(v) and all(isinstance(s, str) and s.strip() for s in v),
    'all_of': lambda v: isinstance(v, list) and bool(v) and all(isinstance(s, str) and s.strip() for s in v),
}


def _validate_rubric_criteria(rubric, expected_points_sum):
    """Validate a rubric (list of criterion objects) and return it normalized.

    Raises serializers.ValidationError on bad shape. When `expected_points_sum`
    is not None, enforces that sum(criterion.points) == expected_points_sum.
    """
    if not isinstance(rubric, list):
        raise serializers.ValidationError('rubric must be a list of criterion objects.')

    # Empty rubric is allowed during draft authoring; grading just returns 0.
    if not rubric:
        return rubric

    errors = []
    total_points = 0
    for idx, criterion in enumerate(rubric):
        if not isinstance(criterion, dict):
            errors.append(f'criterion {idx}: must be an object.')
            continue

        ctype = criterion.get('type')
        if ctype not in _RUBRIC_CRITERION_VALUE_VALIDATORS:
            errors.append(
                f'criterion {idx}: unknown type {ctype!r}. '
                f'Valid types: {sorted(_RUBRIC_CRITERION_VALUE_VALIDATORS)}.'
            )
            continue

        value = criterion.get('value')
        if not _RUBRIC_CRITERION_VALUE_VALIDATORS[ctype](value):
            errors.append(f'criterion {idx}: value is invalid for type {ctype!r}.')
            continue

        if ctype == 'regex':
            try:
                re.compile(value)
            except re.error as exc:
                errors.append(f'criterion {idx}: regex failed to compile: {exc}.')
                continue

        points = criterion.get('points')
        if not isinstance(points, int) or isinstance(points, bool) or points < 0:
            errors.append(f'criterion {idx}: points must be a non-negative integer.')
            continue
        total_points += points

    if errors:
        raise serializers.ValidationError(errors)

    if expected_points_sum is not None and total_points != expected_points_sum:
        raise serializers.ValidationError(
            f'sum of criterion.points ({total_points}) must equal question.points '
            f'({expected_points_sum}).'
        )
    return rubric


class QuizSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(read_only=True)
    question_count = serializers.SerializerMethodField()
    created_by = InstructorBriefSerializer(read_only=True)
    last_edited_by = InstructorBriefSerializer(read_only=True)

    class Meta:
        model = Quiz
        fields = [
            'id', 'section_id', 'title', 'description',
            'question_count',
            'created_by', 'last_edited_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_question_count(self, obj):
        return obj.questions.count()


class QuizCreateUpdateSerializer(serializers.ModelSerializer):
    section = serializers.PrimaryKeyRelatedField(
        queryset=CourseSection.objects.all(),
        required=False,
    )

    class Meta:
        model = Quiz
        fields = ['section', 'title', 'description']

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Quiz title must be at least 2 characters long.')
        return title

    def validate(self, attrs):
        # Section is only required on creation; updates never need to re-supply it.
        if self.instance is not None:
            attrs.pop('section', None)
            return attrs

        if not attrs.get('section'):
            section = self.context.get('section')
            if not section:
                raise serializers.ValidationError({'section': 'Section is required.'})
            attrs['section'] = section
        return attrs

    def create(self, validated_data):
        return Quiz.objects.create(**validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('section', None)  # section is immutable after creation
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# ---------------------------------------------------------------------------
# QuizQuestion serializer
# ---------------------------------------------------------------------------

class QuizQuestionSerializer(serializers.ModelSerializer):
    quiz_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = QuizQuestion
        fields = ['id', 'quiz_id', 'question_text', 'position']
        read_only_fields = ['id', 'quiz_id']

    def validate_question_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Question text cannot be empty.')
        return text

    def create(self, validated_data):
        quiz = self.context['quiz']
        return QuizQuestion.objects.create(quiz=quiz, **validated_data)

    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# ---------------------------------------------------------------------------
# QuizAnswer serializer
# ---------------------------------------------------------------------------

class QuizAnswerSerializer(serializers.ModelSerializer):
    question_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = QuizAnswer
        fields = ['id', 'question_id', 'answer_text', 'is_correct']
        read_only_fields = ['id', 'question_id']

    def validate_answer_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Answer text cannot be empty.')
        return text

    # A QuizQuestion is single-correct

    def create(self, validated_data):
        question = self.context['question']
        with transaction.atomic():
            if validated_data.get('is_correct'):
                QuizAnswer.objects.filter(
                    question=question, is_correct=True
                ).update(is_correct=False)
            return QuizAnswer.objects.create(question=question, **validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            if validated_data.get('is_correct'):
                QuizAnswer.objects.filter(
                    question=instance.question, is_correct=True
                ).exclude(pk=instance.pk).update(is_correct=False)
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            return instance


# ---------------------------------------------------------------------------
# Coding exercise serializers (instructor-facing; no learner serializers in Part 1)
# ---------------------------------------------------------------------------

class CodingExerciseSerializer(serializers.ModelSerializer):
    # Instructor-only serializer: solution_code + evaluation_script must never
    # ride into a learner-facing response (learner classes don't declare them).
    section_id = serializers.IntegerField(read_only=True)
    created_by = InstructorBriefSerializer(read_only=True)
    last_edited_by = InstructorBriefSerializer(read_only=True)

    class Meta:
        model = CodingExercise
        fields = [
            'id', 'section_id', 'title', 'description', 'language',
            'starter_code', 'solution_code', 'evaluation_script', 'time_limit_ms',
            'created_by', 'last_edited_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class CodingExerciseCreateUpdateSerializer(serializers.ModelSerializer):

    class Meta:
        model = CodingExercise
        fields = [
            'title', 'description', 'language',
            'starter_code', 'solution_code', 'evaluation_script', 'time_limit_ms',
        ]

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 3:
            raise serializers.ValidationError('Title must be at least 3 characters long.')
        return title


# ---------------------------------------------------------------------------
# Assignment serializers
# ---------------------------------------------------------------------------

def _request_user_is_instructor(request) -> bool:
    user = getattr(request, 'user', None)
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and getattr(user, 'user_type', None) == 'instructor'
    )


class AssignmentQuestionSerializer(serializers.ModelSerializer):
    assignment_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssignmentQuestion
        fields = [
            'id', 'assignment_id', 'question_text', 'model_answer',
            'rubric', 'points', 'hint', 'position',
        ]
        read_only_fields = ['id', 'assignment_id', 'position']

    def validate_question_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Question text cannot be empty.')
        return text

    def validate(self, attrs):
        # Cross-field check: sum(criterion.points) must equal question.points.
        # On partial updates either side may be absent — fall back to the
        # instance's current value so we still get a real comparison.
        if 'rubric' in attrs or 'points' in attrs:
            rubric = attrs.get(
                'rubric', getattr(self.instance, 'rubric', []) if self.instance else []
            )
            points = attrs.get(
                'points', getattr(self.instance, 'points', 0) if self.instance else 0
            )
            _validate_rubric_criteria(rubric, expected_points_sum=points)
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # model_answer and rubric are instructor-only; strip both for non-instructor callers.
        request = self.context.get('request')
        if not _request_user_is_instructor(request):
            data.pop('model_answer', None)
            data.pop('rubric', None)
        return data


class AssignmentSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(read_only=True)
    questions = AssignmentQuestionSerializer(many=True, read_only=True)
    # Sum of question.points — useful for the authoring UI to surface
    # "you've allocated X of Y declared points to questions." Distinct from
    # `total_score` (the instructor-declared total worth).
    max_score = serializers.SerializerMethodField()
    created_by = InstructorBriefSerializer(read_only=True)
    last_edited_by = InstructorBriefSerializer(read_only=True)

    class Meta:
        model = Assignment
        fields = [
            'id', 'section_id', 'title', 'description', 'instructions',
            'total_score', 'passing_score', 'max_score', 'questions',
            'created_by', 'last_edited_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_max_score(self, obj) -> int:
        # NB: this is the sum-of-question-points, not the assignment's
        # declared total. Both fields are intentionally exposed — the
        # authoring UI uses the gap between them as a sanity check.

        # Prefer annotated value from queryset (O(1) across lists).
        annotated_total = obj.__dict__.get('max_score')
        if annotated_total is not None:
            return int(annotated_total)

        # If questions were prefetched, compute locally without extra DB hits.
        prefetched = getattr(obj, '_prefetched_objects_cache', {})
        if 'questions' in prefetched:
            return sum((q.points or 0) for q in prefetched['questions'])

        # Fallback for isolated objects that were not annotated/prefetched.
        from django.db.models import Sum

        return obj.questions.aggregate(total=Sum('points'))['total'] or 0


# ---------------------------------------------------------------------------
# Admin-review serializers — full depth (correct answers, model answers,
# rubric), unlike the stripped-for-non-instructor fields above. Read-only;
# consumed only by CourseAdminCurriculumView.
# ---------------------------------------------------------------------------

class AdminQuizAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuizAnswer
        fields = ['id', 'answer_text', 'is_correct']
        read_only_fields = fields


class AdminQuizQuestionSerializer(serializers.ModelSerializer):
    answers = AdminQuizAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = QuizQuestion
        fields = ['id', 'question_text', 'position', 'answers']
        read_only_fields = fields


class AdminQuizDetailSerializer(serializers.ModelSerializer):
    questions = AdminQuizQuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Quiz
        fields = ['id', 'title', 'description', 'questions']
        read_only_fields = fields


class AdminAssignmentQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssignmentQuestion
        fields = ['id', 'question_text', 'model_answer', 'rubric', 'points', 'hint', 'position']
        read_only_fields = fields


class AdminAssignmentDetailSerializer(serializers.ModelSerializer):
    questions = AdminAssignmentQuestionSerializer(many=True, read_only=True)
    max_score = serializers.SerializerMethodField()

    class Meta:
        model = Assignment
        fields = [
            'id', 'title', 'description', 'instructions',
            'total_score', 'passing_score', 'max_score', 'questions',
        ]
        read_only_fields = fields

    def get_max_score(self, obj) -> int:
        prefetched = getattr(obj, '_prefetched_objects_cache', {})
        if 'questions' in prefetched:
            return sum((q.points or 0) for q in prefetched['questions'])
        from django.db.models import Sum
        return obj.questions.aggregate(total=Sum('points'))['total'] or 0


class AssignmentCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = ['title', 'description', 'instructions', 'total_score', 'passing_score']

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Assignment title must be at least 2 characters long.')
        return title

    def validate(self, attrs):
        # passing_score must not exceed total_score. On partial updates either
        # side may be absent — fall back to the instance value so we still
        # get a real comparison.
        total = attrs.get(
            'total_score',
            getattr(self.instance, 'total_score', 0) if self.instance else 0,
        )
        passing = attrs.get(
            'passing_score',
            getattr(self.instance, 'passing_score', 0) if self.instance else 0,
        )
        if passing > total:
            raise serializers.ValidationError({
                'passing_score': (
                    f'passing_score ({passing}) cannot exceed total_score ({total}).'
                ),
            })
        return attrs
