from rest_framework import serializers

from courses.models import (
    Assignment,
    AssignmentQuestion,
    CodingExercise,
    CodingExerciseLanguageConfig,
    CodingTestCase,
    CourseSection,
    Quiz,
    QuizAnswer,
    QuizQuestion,
)


class QuizSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(read_only=True)
    question_count = serializers.SerializerMethodField()

    class Meta:
        model = Quiz
        fields = [
            'id', 'section_id', 'title', 'description',
            'question_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_question_count(self, obj):
        return obj.questions.count()


class QuizCreateUpdateSerializer(serializers.ModelSerializer):
    # Required when creating via POST /api/quizzes/ (section in body).
    # Optional when creating via section-contents endpoint (section in context).
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

        # On create: section may come from context (contents endpoint) or body (quizzes endpoint).
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

    def validate(self, attrs):
        # Determine whether the answer will be marked correct after this operation.
        is_correct = attrs.get('is_correct', getattr(self.instance, 'is_correct', False))
        if not is_correct:
            return attrs

        # Resolve the question: from context on create, from instance on update.
        if self.instance is not None:
            question = self.instance.question
        else:
            question = self.context.get('question')

        if question is not None:
            qs = QuizAnswer.objects.filter(question=question, is_correct=True)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {'is_correct': 'A correct answer already exists for this question.'}
                )
        return attrs

    def create(self, validated_data):
        question = self.context['question']
        return QuizAnswer.objects.create(question=question, **validated_data)

    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# ---------------------------------------------------------------------------
# Coding exercise serializers (instructor-facing; no learner serializers in Part 1)
# ---------------------------------------------------------------------------

class CodingTestCaseSerializer(serializers.ModelSerializer):
    exercise_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = CodingTestCase
        fields = ['id', 'exercise_id', 'input_data', 'expected_output', 'is_hidden', 'explanation', 'position']
        read_only_fields = ['id', 'exercise_id']


class CodingExerciseLanguageConfigSerializer(serializers.ModelSerializer):
    exercise_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = CodingExerciseLanguageConfig
        fields = ['id', 'exercise_id', 'language', 'starter_code', 'solution_code']
        read_only_fields = ['id', 'exercise_id']


class CodingExerciseSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(read_only=True)
    language_configs = CodingExerciseLanguageConfigSerializer(many=True, read_only=True)
    test_cases = CodingTestCaseSerializer(many=True, read_only=True)

    class Meta:
        model = CodingExercise
        fields = [
            'id', 'section_id', 'title', 'description', 'problem_statement',
            'difficulty', 'default_language', 'supported_languages', 'time_limit_ms',
            'language_configs', 'test_cases', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class CodingExerciseCreateUpdateSerializer(serializers.ModelSerializer):
    _VALID_LANGUAGES = ['python', 'javascript', 'cpp', 'java']

    class Meta:
        model = CodingExercise
        fields = [
            'title', 'description', 'problem_statement',
            'difficulty', 'default_language', 'supported_languages', 'time_limit_ms',
        ]
        extra_kwargs = {
            'supported_languages': {'required': True},
        }

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 3:
            raise serializers.ValidationError('Title must be at least 3 characters long.')
        return title

    def validate_supported_languages(self, value):
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError('supported_languages must be a non-empty list.')
        invalid = [lang for lang in value if lang not in self._VALID_LANGUAGES]
        if invalid:
            raise serializers.ValidationError(
                f'Invalid languages: {invalid}. Must be one of {self._VALID_LANGUAGES}.'
            )
        return value

    def validate(self, attrs):
        default_language = attrs.get('default_language')
        supported_languages = attrs.get('supported_languages')

        if self.instance is not None:
            if default_language is None:
                default_language = self.instance.default_language
            if supported_languages is None:
                supported_languages = self.instance.supported_languages
        else:
            model = self.Meta.model
            if default_language is None:
                default_language = model._meta.get_field('default_language').get_default()
            if supported_languages is None:
                supported_languages = model._meta.get_field('supported_languages').get_default()

        if default_language not in supported_languages:
            raise serializers.ValidationError(
                {'default_language': 'default_language must be in supported_languages.'}
            )
        return attrs


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
            'points', 'hint', 'position',
        ]
        read_only_fields = ['id', 'assignment_id', 'position']

    def validate_question_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Question text cannot be empty.')
        return text

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # model_answer is instructor-only; strip it for everyone else.
        request = self.context.get('request')
        if not _request_user_is_instructor(request):
            data.pop('model_answer', None)
        return data


class AssignmentSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(read_only=True)
    questions = AssignmentQuestionSerializer(many=True, read_only=True)
    max_score = serializers.SerializerMethodField()

    class Meta:
        model = Assignment
        fields = [
            'id', 'section_id', 'title', 'description', 'instructions',
            'passing_score', 'max_score', 'questions',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_max_score(self, obj) -> int:
        # Prefer annotated value from queryset (O(1) across lists).
        annotated_total = getattr(obj, 'max_score', None)
        if annotated_total is not None:
            return int(annotated_total)

        # If questions were prefetched, compute locally without extra DB hits.
        prefetched = getattr(obj, '_prefetched_objects_cache', {})
        if 'questions' in prefetched:
            return sum((q.points or 0) for q in prefetched['questions'])

        # Fallback for isolated objects that were not annotated/prefetched.
        from django.db.models import Sum

        return obj.questions.aggregate(total=Sum('points'))['total'] or 0


class AssignmentCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = ['title', 'description', 'instructions', 'passing_score']

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Assignment title must be at least 2 characters long.')
        return title
