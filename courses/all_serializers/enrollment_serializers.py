from rest_framework import serializers

from courses.all_serializers.course_serializers import (
    CourseCategoryBriefSerializer,
    CourseAudienceSerializer,
    CourseLearningObjectiveSerializer,
    CoursePreRequisiteSerializer,
    InstructorBriefSerializer,
    PartnerInstitutionBriefSerializer,
)
from courses.models import Enrollment, NidusCourse


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


class CatalogCourseDetailSerializer(serializers.ModelSerializer):
    """Full public detail for a single course (no curriculum content)."""

    instructors = InstructorBriefSerializer(read_only=True, many=True)
    partner_institutions = PartnerInstitutionBriefSerializer(read_only=True, many=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    learning_objectives = CourseLearningObjectiveSerializer(read_only=True, many=True)
    prerequisites = CoursePreRequisiteSerializer(read_only=True, many=True)
    audiences = CourseAudienceSerializer(read_only=True, many=True)
    total_sections = serializers.SerializerMethodField()
    total_content_items = serializers.SerializerMethodField()

    class Meta:
        model = NidusCourse
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail', 'price',
            'language', 'level', 'duration_minutes',
            'instructors', 'partner_institutions', 'category',
            'learning_objectives', 'prerequisites', 'audiences',
            'total_sections', 'total_content_items', 'published_at',
        ]
        read_only_fields = fields

    def get_total_sections(self, obj):
        return obj.sections.count()

    def get_total_content_items(self, obj):
        from courses.models import SectionContent
        return SectionContent.objects.filter(section__course=obj).count()


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


class EnrollmentDetailSerializer(serializers.ModelSerializer):
    """Detailed enrollment view for a single course dashboard."""

    course = CatalogCourseDetailSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            'id', 'course', 'enrollment_type', 'is_active',
            'progress_percent', 'completed_at', 'last_accessed_at',
            'created_at',
        ]
        read_only_fields = fields
