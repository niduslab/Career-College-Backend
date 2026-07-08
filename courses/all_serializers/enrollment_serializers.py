from rest_framework import serializers

from courses.all_serializers.course_serializers import (
    CourseCategoryBriefSerializer,
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
    partner_institution = PartnerInstitutionBriefSerializer(read_only=True, allow_null=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    total_sections = serializers.SerializerMethodField()
    total_content_items = serializers.SerializerMethodField()
    sections = serializers.SerializerMethodField()

    class Meta:
        model = NidusCourse
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail', 'price',
            'language', 'level', 'duration_minutes',
            'instructors', 'partner_institution', 'category',
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
# My-Courses detail serializer — slim metadata payload for the course header.
#
# The full curriculum tree is no longer returned here; learners fetch the
# curriculum outline from `/learn/<slug>/curriculum/` and per-item content
# from `/learn/<thing>/<id>/`. This endpoint only returns course-level
# metadata + the caller's enrollment status (or instructor flag for preview).
# ---------------------------------------------------------------------------

class _MyCourseMetaSerializer(serializers.ModelSerializer):
    """Course metadata block: identity, instructors, objectives, totals."""

    instructors = InstructorBriefSerializer(read_only=True, many=True)
    partner_institution = PartnerInstitutionBriefSerializer(read_only=True, allow_null=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    total_sections = serializers.SerializerMethodField()
    total_content_items = serializers.SerializerMethodField()

    class Meta:
        model = NidusCourse
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail', 'price',
            'language', 'level', 'duration_minutes', 'status', 'is_published',
            'published_at', 'instructors', 'partner_institution', 'category',
            'learning_objectives', 'prerequisites', 'audiences',
            'total_sections', 'total_content_items',
        ]
        read_only_fields = fields

    def get_total_sections(self, course):
        return course.sections.count()

    def get_total_content_items(self, course):
        return SectionContent.objects.filter(section__course=course).count()


class MyCourseDetailSerializer(serializers.Serializer):
    """
    Slim metadata payload for `GET /my-courses/<slug>/`.

    The frontend pairs this with `/learn/<slug>/curriculum/` to render the
    Udemy-style course player: this response provides the header (title,
    instructors, objectives, overall progress); the curriculum endpoint
    provides the sidebar; per-item endpoints provide playable content.
    """

    is_instructor = serializers.SerializerMethodField()
    enrollment = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()

    def get_is_instructor(self, _course):
        return bool(self.context.get('is_instructor'))

    def get_enrollment(self, _course):
        enrollment = self.context.get('enrollment')
        if not enrollment:
            return None
        return EnrollmentBriefSerializer(enrollment).data

    def get_course(self, course):
        return _MyCourseMetaSerializer(course).data


