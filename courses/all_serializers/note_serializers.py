from rest_framework import serializers

from courses.all_models.note_models import LearnerNote
from courses.services.note_service import MAX_NOTE_TAGS


class _NoteCourseBriefSerializer(serializers.Serializer):
    """Minimal course descriptor — a note list must not drag a full card per row."""

    id = serializers.IntegerField()
    title = serializers.CharField()
    slug = serializers.CharField()


class _NoteLectureBriefSerializer(serializers.Serializer):
    """Minimal lecture descriptor for the note's anchor chip."""

    id = serializers.IntegerField()
    title = serializers.CharField()
    lecture_type = serializers.CharField()


class LearnerNoteReadSerializer(serializers.ModelSerializer):
    """Read shape for both list and detail."""

    course = _NoteCourseBriefSerializer(read_only=True, allow_null=True)
    lecture = _NoteLectureBriefSerializer(read_only=True, allow_null=True)

    class Meta:
        model = LearnerNote
        fields = [
            'id', 'title', 'body', 'tags', 'color', 'is_pinned',
            'timestamp_seconds', 'course', 'lecture',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class LearnerNoteWriteSerializer(serializers.Serializer):
    """Write shape for POST and PATCH.

    A plain Serializer rather than a ModelSerializer, mirroring
    CourseReviewWriteSerializer: FK resolution and the course/lecture
    consistency rule belong to the service, and `partial=True` gives clean
    PATCH semantics where only supplied keys reach validated_data.
    """

    course_slug = serializers.SlugField(required=False, allow_null=True)
    lecture_id = serializers.IntegerField(required=False, allow_null=True)
    timestamp_seconds = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    body = serializers.CharField(allow_blank=False)
    tags = serializers.ListField(
        # Blanks are allowed through the child so `validate_tags` can drop
        # them, rather than rejecting a payload over a stray empty entry.
        child=serializers.CharField(max_length=40, allow_blank=True),
        required=False,
        max_length=MAX_NOTE_TAGS,
    )
    color = serializers.ChoiceField(choices=LearnerNote.Color.choices, required=False)
    is_pinned = serializers.BooleanField(required=False)

    def validate_tags(self, value):
        """Strip, lowercase, drop empties, dedupe while preserving order."""
        cleaned = [tag.strip().lower() for tag in value]
        cleaned = [tag for tag in cleaned if tag]
        return list(dict.fromkeys(cleaned))

    def validate(self, attrs):
        if attrs.get('timestamp_seconds') is not None and not attrs.get('lecture_id'):
            raise serializers.ValidationError(
                {'timestamp_seconds': 'A timestamp requires lecture_id.'}
            )
        return attrs
