from rest_framework import serializers

from courses.all_serializers.course_serializers import (
    CourseCategoryBriefSerializer,
    CourseAudienceSerializer,
    CourseLearningObjectiveSerializer,
    CoursePreRequisiteSerializer,
    InstructorBriefSerializer,
    PartnerInstitutionBriefSerializer,
)
from courses.all_serializers.content_serializers import (
    _normalize_media_relative_path,
    _normalize_renditions_playlists,
)
from courses.models import (
    Enrollment,
    Lecture,
    NidusCourse,
    SectionContent,
)


# ---------------------------------------------------------------------------
# Public catalog serializers (no auth required)
# ---------------------------------------------------------------------------

class CatalogCourseListSerializer(serializers.ModelSerializer):
    """Compact course card for catalog browse lists."""

    instructors = InstructorBriefSerializer(read_only=True, many=True)
    category = CourseCategoryBriefSerializer(read_only=True)

    class Meta:
        model = NidusCourse
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail', 'price',
            'language', 'level', 'duration_minutes',
            'instructors', 'category', 'published_at',
        ]
        read_only_fields = fields


class _CatalogCurriculumItemSerializer(serializers.Serializer):
    """
    Catalog-safe view of one SectionContent row.

    Lecture rows expose duration + is_preview, and the master playlist URL
    only when is_preview=True. Quiz / coding / assignment rows expose
    title only — no questions, test cases, or model answers.
    """

    id = serializers.IntegerField()
    item_type = serializers.CharField()
    position = serializers.IntegerField()
    object_id = serializers.IntegerField()
    content = serializers.SerializerMethodField()

    def get_content(self, obj):
        item_type = obj.item_type
        lectures = self.context.get('lectures', {})
        quizzes = self.context.get('quizzes', {})
        coding_exercises = self.context.get('coding_exercises', {})
        assignments = self.context.get('assignments', {})
        lecture_durations = self.context.get('lecture_durations', {})

        if item_type == SectionContent.ItemType.LECTURE:
            lecture = lectures.get(obj.object_id)
            if not lecture:
                return None
            payload = {
                'id': lecture.id,
                'title': lecture.title,
                'lecture_type': lecture.lecture_type,
                'is_preview': lecture.is_preview,
                'duration_seconds': lecture_durations.get(lecture.id),
            }
            if lecture.is_preview and lecture.lecture_type == Lecture.LectureType.VIDEO:
                payload['preview_video_url'] = _normalize_media_relative_path(
                    lecture.stream_master_playlist
                )
                payload['preview_renditions'] = _normalize_renditions_playlists(
                    lecture.stream_renditions
                )
            return payload

        if item_type == SectionContent.ItemType.QUIZ:
            quiz = quizzes.get(obj.object_id)
            if quiz:
                return {'id': quiz.id, 'title': quiz.title}

        if item_type == SectionContent.ItemType.CODING:
            ex = coding_exercises.get(obj.object_id)
            if ex:
                return {'id': ex.id, 'title': ex.title, 'difficulty': ex.difficulty}

        if item_type == SectionContent.ItemType.ASSIGNMENT:
            assignment = assignments.get(obj.object_id)
            if assignment:
                return {'id': assignment.id, 'title': assignment.title}

        return None


class _CatalogCurriculumSectionSerializer(serializers.Serializer):
    """One section in the catalog curriculum tree (title + content rows)."""

    id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    position = serializers.IntegerField()
    total_items = serializers.SerializerMethodField()
    contents = serializers.SerializerMethodField()

    def get_total_items(self, section):
        contents_by_section = self.context.get('contents_by_section', {})
        return len(contents_by_section.get(section.id, []))

    def get_contents(self, section):
        contents_by_section = self.context.get('contents_by_section', {})
        rows = contents_by_section.get(section.id, [])
        return _CatalogCurriculumItemSerializer(rows, many=True, context=self.context).data


class CatalogCourseDetailSerializer(serializers.ModelSerializer):
    """Public detail for a single course.

    Includes course metadata + curriculum outline. Lecture rows expose
    duration and `is_preview`; the streaming URL is only included for
    lectures explicitly marked as preview by the instructor.
    """

    instructors = InstructorBriefSerializer(read_only=True, many=True)
    partner_institutions = PartnerInstitutionBriefSerializer(read_only=True, many=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    learning_objectives = CourseLearningObjectiveSerializer(read_only=True, many=True)
    prerequisites = CoursePreRequisiteSerializer(read_only=True, many=True)
    audiences = CourseAudienceSerializer(read_only=True, many=True)
    total_sections = serializers.SerializerMethodField()
    total_content_items = serializers.SerializerMethodField()
    sections = serializers.SerializerMethodField()

    class Meta:
        model = NidusCourse
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail', 'price',
            'language', 'level', 'duration_minutes',
            'instructors', 'partner_institutions', 'category',
            'learning_objectives', 'prerequisites', 'audiences',
            'total_sections', 'total_content_items', 'sections',
            'published_at',
        ]
        read_only_fields = fields

    def get_total_sections(self, obj):
        sections = self.context.get('sections')
        if sections is not None:
            return len(sections)
        return obj.sections.count()

    def get_total_content_items(self, obj):
        contents_by_section = self.context.get('contents_by_section')
        if contents_by_section is not None:
            return sum(len(items) for items in contents_by_section.values())
        return SectionContent.objects.filter(section__course=obj).count()

    def get_sections(self, obj):
        sections = self.context.get('sections', [])
        return _CatalogCurriculumSectionSerializer(
            sections, many=True, context=self.context,
        ).data


# ---------------------------------------------------------------------------
# Enrollment serializers (authenticated learner)
# ---------------------------------------------------------------------------

class EnrollmentSerializer(serializers.ModelSerializer):
    """Read-only enrollment record with nested course summary."""

    course = CatalogCourseListSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            'id', 'course', 'enrollment_type', 'is_active',
            'progress_percent', 'completed_at', 'last_accessed_at',
            'created_at',
        ]
        read_only_fields = fields


class EnrollmentBriefSerializer(serializers.ModelSerializer):
    """Enrollment record without the nested course (course is the parent)."""

    class Meta:
        model = Enrollment
        fields = [
            'id', 'enrollment_type', 'is_active',
            'progress_percent', 'completed_at', 'last_accessed_at',
            'created_at',
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Consumption serializers — used by the learner/instructor "learn" endpoint.
#
# These are deliberately separate from instructor authoring serializers so
# that sensitive fields (`solution_code`, hidden test cases, `model_answer`,
# `is_correct` on quiz answers) never leak to unauthorized callers.
# ---------------------------------------------------------------------------

def _is_instructor_context(context):
    return bool(context.get('is_instructor'))


class _ConsumptionLectureSerializer(serializers.Serializer):
    """Full lecture payload (HLS URLs + article) plus learner progress."""

    id = serializers.IntegerField()
    title = serializers.CharField()
    lecture_type = serializers.CharField()
    is_preview = serializers.BooleanField()
    article_content = serializers.CharField()
    stream_master_playlist = serializers.SerializerMethodField()
    stream_renditions = serializers.SerializerMethodField()
    duration_seconds = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    def get_stream_master_playlist(self, lecture):
        return _normalize_media_relative_path(lecture.stream_master_playlist)

    def get_stream_renditions(self, lecture):
        return _normalize_renditions_playlists(lecture.stream_renditions)

    def get_duration_seconds(self, lecture):
        durations = self.context.get('lecture_durations', {})
        return durations.get(lecture.id)

    def get_progress(self, lecture):
        if _is_instructor_context(self.context):
            return None
        progress_map = self.context.get('watch_progress', {})
        wp = progress_map.get(lecture.id)
        if not wp:
            return {'watched_seconds': 0, 'is_completed': False, 'last_watched_at': None}
        return {
            'watched_seconds': wp.watched_seconds,
            'is_completed': wp.is_completed,
            'last_watched_at': wp.last_watched_at,
        }


class _ConsumptionQuizAnswerSerializer(serializers.Serializer):
    """Answer option. `is_correct` is included for instructors only."""

    id = serializers.IntegerField()
    answer_text = serializers.CharField()

    def to_representation(self, answer):
        data = super().to_representation(answer)
        if _is_instructor_context(self.context):
            data['is_correct'] = answer.is_correct
        return data


class _ConsumptionQuizQuestionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    question_text = serializers.CharField()
    position = serializers.IntegerField()
    answers = serializers.SerializerMethodField()

    def get_answers(self, question):
        # Use prefetched cache when present to avoid N+1.
        prefetched = getattr(question, '_prefetched_objects_cache', {})
        if 'answers' in prefetched:
            answer_objs = prefetched['answers']
        else:
            answer_objs = list(question.answers.all())
        return _ConsumptionQuizAnswerSerializer(
            answer_objs, many=True, context=self.context,
        ).data


class _ConsumptionQuizSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    questions = serializers.SerializerMethodField()

    def get_questions(self, quiz):
        prefetched = getattr(quiz, '_prefetched_objects_cache', {})
        if 'questions' in prefetched:
            question_objs = prefetched['questions']
        else:
            question_objs = list(quiz.questions.all())
        return _ConsumptionQuizQuestionSerializer(
            question_objs, many=True, context=self.context,
        ).data


class _ConsumptionAssignmentQuestionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    question_text = serializers.CharField()
    points = serializers.IntegerField()
    hint = serializers.CharField()
    position = serializers.IntegerField()

    def to_representation(self, question):
        data = super().to_representation(question)
        # model_answer is instructor-only.
        if _is_instructor_context(self.context):
            data['model_answer'] = question.model_answer
        return data


class _ConsumptionAssignmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    instructions = serializers.CharField()
    passing_score = serializers.IntegerField()
    questions = serializers.SerializerMethodField()

    def get_questions(self, assignment):
        prefetched = getattr(assignment, '_prefetched_objects_cache', {})
        if 'questions' in prefetched:
            question_objs = prefetched['questions']
        else:
            question_objs = list(assignment.questions.all())
        return _ConsumptionAssignmentQuestionSerializer(
            question_objs, many=True, context=self.context,
        ).data


class _ConsumptionCodingLanguageConfigSerializer(serializers.Serializer):
    """Per-language config. solution_code is instructor-only."""

    id = serializers.IntegerField()
    language = serializers.CharField()
    starter_code = serializers.CharField()

    def to_representation(self, cfg):
        data = super().to_representation(cfg)
        if _is_instructor_context(self.context):
            data['solution_code'] = cfg.solution_code
        return data


class _ConsumptionCodingTestCaseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    input_data = serializers.CharField()
    expected_output = serializers.CharField()
    explanation = serializers.CharField()
    position = serializers.IntegerField()

    def to_representation(self, tc):
        data = super().to_representation(tc)
        if _is_instructor_context(self.context):
            data['is_hidden'] = tc.is_hidden
        return data


class _ConsumptionCodingExerciseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    problem_statement = serializers.CharField()
    difficulty = serializers.CharField()
    default_language = serializers.CharField()
    supported_languages = serializers.ListField(child=serializers.CharField())
    time_limit_ms = serializers.IntegerField()
    language_configs = serializers.SerializerMethodField()
    test_cases = serializers.SerializerMethodField()

    def get_language_configs(self, exercise):
        prefetched = getattr(exercise, '_prefetched_objects_cache', {})
        configs = prefetched['language_configs'] if 'language_configs' in prefetched else list(exercise.language_configs.all())
        return _ConsumptionCodingLanguageConfigSerializer(
            configs, many=True, context=self.context,
        ).data

    def get_test_cases(self, exercise):
        prefetched = getattr(exercise, '_prefetched_objects_cache', {})
        cases = prefetched['test_cases'] if 'test_cases' in prefetched else list(exercise.test_cases.all())
        # Hidden test cases are never exposed to learners.
        if not _is_instructor_context(self.context):
            cases = [tc for tc in cases if not tc.is_hidden]
        return _ConsumptionCodingTestCaseSerializer(
            cases, many=True, context=self.context,
        ).data


class _ConsumptionItemSerializer(serializers.Serializer):
    """One SectionContent row in the consumption tree, fully expanded."""

    id = serializers.IntegerField()
    item_type = serializers.CharField()
    position = serializers.IntegerField()
    object_id = serializers.IntegerField()
    content = serializers.SerializerMethodField()

    def get_content(self, obj):
        lectures = self.context.get('lectures', {})
        quizzes = self.context.get('quizzes', {})
        coding_exercises = self.context.get('coding_exercises', {})
        assignments = self.context.get('assignments', {})

        if obj.item_type == SectionContent.ItemType.LECTURE:
            lecture = lectures.get(obj.object_id)
            if lecture:
                return _ConsumptionLectureSerializer(lecture, context=self.context).data
        elif obj.item_type == SectionContent.ItemType.QUIZ:
            quiz = quizzes.get(obj.object_id)
            if quiz:
                return _ConsumptionQuizSerializer(quiz, context=self.context).data
        elif obj.item_type == SectionContent.ItemType.CODING:
            ex = coding_exercises.get(obj.object_id)
            if ex:
                return _ConsumptionCodingExerciseSerializer(ex, context=self.context).data
        elif obj.item_type == SectionContent.ItemType.ASSIGNMENT:
            assignment = assignments.get(obj.object_id)
            if assignment:
                return _ConsumptionAssignmentSerializer(assignment, context=self.context).data
        return None


class _ConsumptionSectionSerializer(serializers.Serializer):
    """One section in the consumption tree."""

    id = serializers.IntegerField()
    title = serializers.CharField()
    description = serializers.CharField()
    position = serializers.IntegerField()
    contents = serializers.SerializerMethodField()

    def get_contents(self, section):
        contents_by_section = self.context.get('contents_by_section', {})
        rows = contents_by_section.get(section.id, [])
        return _ConsumptionItemSerializer(rows, many=True, context=self.context).data


class EnrolledCourseContentSerializer(serializers.Serializer):
    """
    Course + full curriculum payload for the "learn" endpoint.

    Used for both enrolled learners and the course's own instructors.
    Sensitive fields (solution_code, hidden test cases, model_answer,
    quiz answer correctness) are only included when context['is_instructor']
    is True.
    """

    is_instructor = serializers.SerializerMethodField()
    enrollment = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()
    sections = serializers.SerializerMethodField()

    def get_is_instructor(self, course):
        return _is_instructor_context(self.context)

    def get_enrollment(self, course):
        enrollment = self.context.get('enrollment')
        if not enrollment:
            return None
        return EnrollmentBriefSerializer(enrollment).data

    def get_course(self, course):
        return _ConsumptionCourseMetaSerializer(course).data

    def get_sections(self, course):
        sections = self.context.get('sections', [])
        return _ConsumptionSectionSerializer(
            sections, many=True, context=self.context,
        ).data


class _ConsumptionCourseMetaSerializer(serializers.ModelSerializer):
    """Course metadata block embedded in the consumption payload."""

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
            'published_at', 'instructors', 'partner_institutions', 'category',
            'learning_objectives', 'prerequisites', 'audiences',
        ]
        read_only_fields = fields


