from django.db import transaction
from rest_framework import serializers

from auth.models import PartnerInstitutionProfile, User
from courses.models import (
    CodingExercise,
    CodingExerciseLanguageConfig,
    CodingTestCase,
    CourseAudience,
    CourseCategory,
    CourseLearningObjective,
    CoursePreRequisite,
    CourseSection,
    Lecture,
    NidusCourse,
    Quiz,
    QuizAnswer,
    QuizQuestion,
    SectionContent,
    VideoAsset,
)


# ---------------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------------

def _normalize_media_relative_path(path_value):
    if not isinstance(path_value, str):
        return path_value
    normalized = path_value.replace('\\', '/').lstrip('/')
    if normalized.startswith('media/'):
        return normalized[len('media/'):]
    return normalized


def _normalize_renditions_playlists(renditions):
    if not isinstance(renditions, list):
        return renditions
    normalized_renditions = []
    for item in renditions:
        if not isinstance(item, dict):
            normalized_renditions.append(item)
            continue
        row = dict(item)
        row['playlist'] = _normalize_media_relative_path(row.get('playlist', ''))
        normalized_renditions.append(row)
    return normalized_renditions


# ---------------------------------------------------------------------------
# Auth / institution brief serializers (unchanged)
# ---------------------------------------------------------------------------

class InstructorBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'full_name', 'email']
        read_only_fields = fields


class PartnerInstitutionBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerInstitutionProfile
        fields = ['id', 'institution_name', 'slug']
        read_only_fields = fields


class CourseCategoryBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseCategory
        fields = ['id', 'name', 'slug']
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Course item serializers (unchanged)
# ---------------------------------------------------------------------------

class CourseLearningObjectiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseLearningObjective
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']

    def validate_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Text cannot be empty.')
        return text


class CoursePreRequisiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoursePreRequisite
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']

    def validate_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Text cannot be empty.')
        return text


class CourseAudienceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseAudience
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']

    def validate_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Text cannot be empty.')
        return text


# ---------------------------------------------------------------------------
# Course serializers (unchanged)
# ---------------------------------------------------------------------------

class NidusCourseSerializer(serializers.ModelSerializer):
    created_by = InstructorBriefSerializer(read_only=True)
    instructors = InstructorBriefSerializer(read_only=True, many=True)
    partner_institutions = PartnerInstitutionBriefSerializer(read_only=True, many=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    learning_objectives = CourseLearningObjectiveSerializer(read_only=True, many=True)
    prerequisites = CoursePreRequisiteSerializer(read_only=True, many=True)
    audiences = CourseAudienceSerializer(read_only=True, many=True)

    class Meta:
        model = NidusCourse
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail', 'price',
            'language', 'level', 'duration_minutes', 'status', 'is_published',
            'rejection_reason', 'published_at', 'created_by', 'instructors',
            'partner_institutions', 'category', 'learning_objectives',
            'prerequisites', 'audiences', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class NidusCourseCreateUpdateSerializer(serializers.ModelSerializer):
    instructors = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.filter(user_type='instructor', is_deleted=False),
        required=False,
    )
    partner_institutions = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=PartnerInstitutionProfile.objects.filter(is_active=True),
        required=False,
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=CourseCategory.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    learning_objectives = CourseLearningObjectiveSerializer(many=True, required=False)
    prerequisites = CoursePreRequisiteSerializer(many=True, required=False)
    audiences = CourseAudienceSerializer(many=True, required=False)

    class Meta:
        model = NidusCourse
        fields = [
            'title', 'description', 'thumbnail', 'price', 'language', 'level',
            'duration_minutes', 'status', 'rejection_reason', 'instructors',
            'partner_institutions', 'category', 'learning_objectives',
            'prerequisites', 'audiences',
        ]

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 5:
            raise serializers.ValidationError('Title must be at least 5 characters long.')
        return title

    def validate(self, attrs):
        status_value = attrs.get('status')
        rejection_reason = attrs.get('rejection_reason', '')
        if status_value == NidusCourse.CourseStatus.REJECTED and not rejection_reason.strip():
            raise serializers.ValidationError(
                {'rejection_reason': 'Rejection reason is required for rejected status.'}
            )
        return attrs

    def _replace_items(self, model_class, course, items):
        model_class.objects.filter(course=course).delete()
        new_objects = [
            model_class(
                course=course,
                text=item['text'].strip(),
                display_order=item.get('display_order', index),
            )
            for index, item in enumerate(items)
            if item.get('text', '').strip()
        ]
        if new_objects:
            model_class.objects.bulk_create(new_objects)

    def create(self, validated_data):
        with transaction.atomic():
            learning_objectives = validated_data.pop('learning_objectives', [])
            prerequisites = validated_data.pop('prerequisites', [])
            audiences = validated_data.pop('audiences', [])
            instructors = validated_data.pop('instructors', [])
            partner_institutions = validated_data.pop('partner_institutions', [])

            request_user = self.context['request'].user
            course = NidusCourse.objects.create(created_by=request_user, **validated_data)

            if not instructors:
                instructors = [request_user]
            elif request_user not in instructors:
                instructors.append(request_user)

            course.instructors.set(instructors)
            course.partner_institutions.set(partner_institutions)
            self._replace_items(CourseLearningObjective, course, learning_objectives)
            self._replace_items(CoursePreRequisite, course, prerequisites)
            self._replace_items(CourseAudience, course, audiences)
            return course

    def update(self, instance, validated_data):
        with transaction.atomic():
            learning_objectives = validated_data.pop('learning_objectives', None)
            prerequisites = validated_data.pop('prerequisites', None)
            audiences = validated_data.pop('audiences', None)
            instructors = validated_data.pop('instructors', None)
            partner_institutions = validated_data.pop('partner_institutions', None)

            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            request_user = self.context['request'].user

            if instructors is not None:
                if request_user not in instructors:
                    instructors.append(request_user)
                instance.instructors.set(instructors)

            if partner_institutions is not None:
                instance.partner_institutions.set(partner_institutions)

            if learning_objectives is not None:
                self._replace_items(CourseLearningObjective, instance, learning_objectives)
            if prerequisites is not None:
                self._replace_items(CoursePreRequisite, instance, prerequisites)
            if audiences is not None:
                self._replace_items(CourseAudience, instance, audiences)

            return instance


# ---------------------------------------------------------------------------
# VideoAsset serializer (unchanged)
# ---------------------------------------------------------------------------

class VideoAssetSerializer(serializers.ModelSerializer):
    master_playlist = serializers.SerializerMethodField()
    renditions = serializers.SerializerMethodField()

    class Meta:
        model = VideoAsset
        fields = [
            'id', 'video_file', 'original_filename', 'mime_type', 'file_size',
            'duration_seconds', 'master_playlist', 'renditions', 'is_active',
            'status', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_master_playlist(self, obj):
        return _normalize_media_relative_path(obj.master_playlist)

    def get_renditions(self, obj):
        return _normalize_renditions_playlists(obj.renditions)


# ---------------------------------------------------------------------------
# Section serializers (unchanged)
# ---------------------------------------------------------------------------

class CourseSectionSerializer(serializers.ModelSerializer):
    course_id = serializers.IntegerField(source='course.id', read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)

    class Meta:
        model = CourseSection
        fields = [
            'id', 'course_id', 'course_title', 'title', 'description',
            'position', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'course_id', 'course_title', 'created_at', 'updated_at']


class CourseSectionCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseSection
        fields = ['title', 'description', 'position']

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Section title must be at least 2 characters long.')
        return title


# ---------------------------------------------------------------------------
# Lecture serializers — position field removed
# ---------------------------------------------------------------------------

class LectureSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(source='section.id', read_only=True)
    stream_master_playlist = serializers.SerializerMethodField()
    stream_renditions = serializers.SerializerMethodField()
    active_video_asset = serializers.SerializerMethodField()

    class Meta:
        model = Lecture
        fields = [
            'id', 'section_id', 'title',
            'content_type', 'article_content',
            'stream_master_playlist', 'stream_renditions', 'transcoding_error',
            'active_video_asset', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_stream_master_playlist(self, obj):
        return _normalize_media_relative_path(obj.stream_master_playlist)

    def get_stream_renditions(self, obj):
        return _normalize_renditions_playlists(obj.stream_renditions)

    def get_active_video_asset(self, obj):
        asset = obj.video_assets.filter(is_active=True).order_by('-created_at').first()
        if not asset:
            return None
        return VideoAssetSerializer(asset).data


class LectureCreateUpdateSerializer(serializers.ModelSerializer):
    video_file = serializers.FileField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Lecture
        fields = ['title', 'content_type', 'article_content', 'video_file']

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Lecture title must be at least 2 characters long.')
        return title

    def validate(self, attrs):
        content_type = attrs.get('content_type')
        article_content = attrs.get('article_content')
        video_file = attrs.get('video_file')

        if self.instance is not None and content_type is None:
            content_type = self.instance.content_type
        if self.instance is not None and article_content is None:
            article_content = self.instance.article_content

        if content_type == Lecture.ContentType.ARTICLE:
            if video_file:
                raise serializers.ValidationError({'video_file': 'Article lectures cannot include video files.'})
            if not (article_content or '').strip():
                raise serializers.ValidationError({'article_content': 'Article lectures require content.'})

        if content_type == Lecture.ContentType.VIDEO:
            if (article_content or '').strip():
                raise serializers.ValidationError({'article_content': 'Video lectures cannot include article content.'})
            creating = self.instance is None
            if creating and not video_file:
                raise serializers.ValidationError({'video_file': 'Video lectures require a video file on creation.'})

        return attrs

    def create(self, validated_data):
        video_file = validated_data.pop('video_file', None)
        section = self.context['section']
        lecture = Lecture.objects.create(section=section, **validated_data)
        if video_file:
            from courses.services import replace_lecture_video_and_enqueue_transcoding
            replace_lecture_video_and_enqueue_transcoding(lecture=lecture, uploaded_file=video_file)
        return lecture

    def update(self, instance, validated_data):
        video_file = validated_data.pop('video_file', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if video_file:
            from courses.services import replace_lecture_video_and_enqueue_transcoding
            replace_lecture_video_and_enqueue_transcoding(lecture=instance, uploaded_file=video_file)
        return instance


# ---------------------------------------------------------------------------
# SectionContent serializer
# Bulk-loading maps (lectures, quizzes) are passed via serializer context
# by the view to avoid N+1 queries.
# ---------------------------------------------------------------------------

class SectionContentSerializer(serializers.ModelSerializer):
    content = serializers.SerializerMethodField()

    class Meta:
        model = SectionContent
        fields = ['id', 'section', 'item_type', 'object_id', 'position', 'content', 'created_at', 'updated_at']
        read_only_fields = fields

    def get_content(self, obj):
        lectures: dict = self.context.get('lectures', {})
        quizzes: dict = self.context.get('quizzes', {})
        coding_exercises: dict = self.context.get('coding_exercises', {})

        if obj.item_type == SectionContent.ItemType.LECTURE:
            lecture = lectures.get(obj.object_id)
            if lecture:
                return {'id': lecture.id, 'title': lecture.title, 'content_type': lecture.content_type}

        elif obj.item_type == SectionContent.ItemType.QUIZ:
            quiz = quizzes.get(obj.object_id)
            if quiz:
                return {'id': quiz.id, 'title': quiz.title}

        elif obj.item_type == SectionContent.ItemType.CODING:
            ex = coding_exercises.get(obj.object_id)
            if ex:
                return {
                    'id': ex.id,
                    'title': ex.title,
                    'difficulty': ex.difficulty,
                    'default_language': ex.default_language,
                }

        return None


# ---------------------------------------------------------------------------
# Quiz serializers
# ---------------------------------------------------------------------------

class QuizSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(source='section.id', read_only=True)
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
    quiz_id = serializers.IntegerField(source='quiz.id', read_only=True)

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
    question_id = serializers.IntegerField(source='question.id', read_only=True)

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
    exercise_id = serializers.IntegerField(source='exercise.id', read_only=True)

    class Meta:
        model = CodingTestCase
        fields = ['id', 'exercise_id', 'input_data', 'expected_output', 'is_hidden', 'explanation', 'position']
        read_only_fields = ['id', 'exercise_id']


class CodingExerciseLanguageConfigSerializer(serializers.ModelSerializer):
    exercise_id = serializers.IntegerField(source='exercise.id', read_only=True)

    class Meta:
        model = CodingExerciseLanguageConfig
        fields = ['id', 'exercise_id', 'language', 'starter_code', 'solution_code']
        read_only_fields = ['id', 'exercise_id']


class CodingExerciseSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(source='section.id', read_only=True)
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

        if default_language and supported_languages:
            if default_language not in supported_languages:
                raise serializers.ValidationError(
                    {'default_language': 'default_language must be in supported_languages.'}
                )
        return attrs
