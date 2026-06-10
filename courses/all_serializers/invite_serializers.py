from rest_framework import serializers

from courses.models import CourseInstructorInvite


class CourseInstructorInviteSerializer(serializers.ModelSerializer):
    """Invitee-facing serializer — includes token for accept/decline links."""

    course_title = serializers.CharField(source='course.title', read_only=True)
    invited_by_name = serializers.CharField(source='invited_by.full_name', read_only=True)
    invited_user_name = serializers.CharField(source='invited_user.full_name', read_only=True)
    invited_user_email = serializers.EmailField(source='invited_user.email', read_only=True)

    class Meta:
        model = CourseInstructorInvite
        fields = [
            'id',
            'course',
            'course_title',
            'invited_by',
            'invited_by_name',
            'invited_user',
            'invited_user_name',
            'invited_user_email',
            'token',
            'status',
            'expires_at',
            'responded_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class CourseInstructorInviteOwnerSerializer(serializers.ModelSerializer):
    """Owner-facing serializer — token excluded (accept/decline key is for invitees only)."""

    course_title = serializers.CharField(source='course.title', read_only=True)
    invited_by_name = serializers.CharField(source='invited_by.full_name', read_only=True)
    invited_user_name = serializers.CharField(source='invited_user.full_name', read_only=True)
    invited_user_email = serializers.EmailField(source='invited_user.email', read_only=True)

    class Meta:
        model = CourseInstructorInvite
        fields = [
            'id',
            'course',
            'course_title',
            'invited_by',
            'invited_by_name',
            'invited_user',
            'invited_user_name',
            'invited_user_email',
            'status',
            'expires_at',
            'responded_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class CourseInstructorInviteCreateSerializer(serializers.Serializer):
    """Write serializer for sending an invite — accepts only the invitee email."""

    email = serializers.EmailField()
