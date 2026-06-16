from rest_framework import serializers

from messaging.models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    """Read serializer for a single message. Includes sender identity + is_own flag."""

    sender_name = serializers.CharField(source='sender.full_name', read_only=True)
    is_own = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id',
            'conversation_id',
            'sender_id',
            'sender_name',
            'body',
            'is_own',
            'created_at',
        ]
        read_only_fields = fields

    def get_is_own(self, obj: Message) -> bool:
        request = self.context.get('request')
        if request is None:
            return False
        return obj.sender_id == request.user.pk


class ConversationSerializer(serializers.ModelSerializer):
    """
    List-level serializer. Returns metadata + per-caller unread_count.

    unread_count performs one COUNT query per conversation row. Acceptable for
    inbox lists (O(10–50) items); optimize with annotation if scale demands.
    """

    learner_name = serializers.CharField(source='learner.full_name', read_only=True)
    instructor_name = serializers.CharField(source='instructor.full_name', read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)
    course_slug = serializers.SlugField(source='course.slug', read_only=True)
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'id',
            'learner_id',
            'learner_name',
            'instructor_id',
            'instructor_name',
            'course_title',
            'course_slug',
            'unread_count',
            'updated_at',
            'created_at',
        ]
        read_only_fields = fields

    def get_unread_count(self, obj: Conversation) -> int:
        request = self.context.get('request')
        if request is None:
            return 0
        user = request.user
        last_read = (
            obj.learner_last_read_at
            if user.pk == obj.learner_id
            else obj.instructor_last_read_at
        )
        qs = Message.objects.filter(conversation=obj, is_deleted=False)
        if last_read is not None:
            qs = qs.filter(created_at__gt=last_read)
        return qs.count()


class ConversationCreateSerializer(serializers.Serializer):
    """Payload for initiating a new conversation (learner → instructor for a course)."""

    course_id = serializers.IntegerField(min_value=1)
    instructor_id = serializers.IntegerField(min_value=1)
    body = serializers.CharField(max_length=5000)

    def validate_body(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('Message body must not be blank.')
        return stripped


class SendMessageSerializer(serializers.Serializer):
    """Payload for sending a follow-up message in an existing conversation."""

    body = serializers.CharField(max_length=5000)

    def validate_body(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('Message body must not be blank.')
        return stripped
