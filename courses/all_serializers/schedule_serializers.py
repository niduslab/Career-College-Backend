from rest_framework import serializers

from courses.all_models.schedule_models import CourseSchedule
from courses.all_serializers.course_serializers import InstructorBriefSerializer, NidusCourseSerializer


class CourseScheduleSerializer(serializers.ModelSerializer):
    """Read serializer for a course schedule (cohort)."""

    created_by = InstructorBriefSerializer(read_only=True)
    last_edited_by = InstructorBriefSerializer(read_only=True)

    class Meta:
        model = CourseSchedule
        fields = [
            'id',
            'course',
            'cohort_label',
            'timezone',
            'enrollment_opens_at',
            'enrollment_closes_at',
            'start_date',
            'end_date',
            'max_seats',
            'status',
            'created_by',
            'last_edited_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class CourseScheduleCreateUpdateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating/patching a schedule.

    `course` comes from the URL (passed via `serializer.save(course=...)`) and
    `status` only moves through the transition endpoints — neither is writable.
    """

    class Meta:
        model = CourseSchedule
        fields = [
            'cohort_label',
            'timezone',
            'enrollment_opens_at',
            'enrollment_closes_at',
            'start_date',
            'end_date',
            'max_seats',
        ]

    def validate(self, attrs):
        def current(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None) if self.instance else None

        opens = current('enrollment_opens_at')
        closes = current('enrollment_closes_at')
        start = current('start_date')
        end = current('end_date')

        errors = {}
        if opens and closes and opens >= closes:
            errors['enrollment_opens_at'] = 'Enrollment must open before it closes.'
        if closes and start and closes > start:
            errors['enrollment_closes_at'] = 'Enrollment must close on or before the start date.'
        if end and start and end <= start:
            errors['end_date'] = 'End date must be after the start date.'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class CourseAdminReviewDetailSerializer(NidusCourseSerializer):
    """
    Admin-review context: base course fields + attached schedules + outline
    stats, so a platform admin can judge a thin cohort outline before approving.
    """

    schedules = CourseScheduleSerializer(many=True, read_only=True)
    outline_stats = serializers.SerializerMethodField()

    class Meta(NidusCourseSerializer.Meta):
        fields = NidusCourseSerializer.Meta.fields + ['schedules', 'outline_stats']
        read_only_fields = fields

    def get_outline_stats(self, obj):
        return obj.content_outline_stats()
