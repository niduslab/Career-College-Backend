from rest_framework import serializers

from courses.all_models.course_models import NidusCourse


class CourseOutlineRequestSerializer(serializers.Serializer):
    """Body for the AI outline-preview endpoint.

    Field-shape validation only — the request is forwarded to the AI service,
    nothing is looked up or written here. Fields mirror the `NidusCourse`
    columns the instructor has already filled in, except `category` (a
    free-text hint, deliberately not a `CourseCategory` id) and
    `extra_instructions` (a free-text steer, also what makes a regenerate
    produce something different).
    """

    title = serializers.CharField(max_length=255)
    description = serializers.CharField(max_length=8000)
    audience = serializers.CharField(max_length=2000)
    prerequisites = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000,
    )
    level = serializers.ChoiceField(
        choices=NidusCourse.CourseLevel.choices,
        required=False, allow_blank=True, default='',
    )
    language = serializers.CharField(required=False, default='English', max_length=50)
    duration_minutes = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=0,
    )
    category = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=120,
    )
    extra_instructions = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=2000,
    )
