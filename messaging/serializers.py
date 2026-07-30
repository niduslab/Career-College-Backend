from rest_framework import serializers

from messaging.models import Conversation, ConversationParticipant, Message


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


class ConversationParticipantSerializer(serializers.ModelSerializer):
    """A single party in a conversation."""

    full_name = serializers.CharField(source='user.full_name', read_only=True)
    user_type = serializers.CharField(source='user.user_type', read_only=True)

    class Meta:
        model = ConversationParticipant
        fields = ['user_id', 'full_name', 'user_type', 'last_read_at']
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    """
    List-level serializer. Returns metadata + participants + per-caller unread_count.

    unread_count performs one COUNT query per conversation row. Acceptable for
    inbox lists (O(10–50) items); optimize with annotation if scale demands.
    """

    participants = ConversationParticipantSerializer(many=True, read_only=True)
    course_title = serializers.SerializerMethodField()
    course_slug = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'id',
            'conversation_type',
            'course_id',
            'course_title',
            'course_slug',
            'participants',
            'unread_count',
            'last_message',
            'updated_at',
            'created_at',
        ]
        read_only_fields = fields

    def get_course_title(self, obj: Conversation):
        return obj.course.title if obj.course_id else None

    def get_course_slug(self, obj: Conversation):
        return obj.course.slug if obj.course_id else None

    def get_last_message(self, obj: Conversation):
        message = (
            Message.objects.filter(conversation=obj, is_deleted=False)
            .order_by('-created_at')
            .first()
        )
        if message is None:
            return None
        return {
            'body': message.body,
            'sender_id': message.sender_id,
            'created_at': message.created_at,
        }

    def get_unread_count(self, obj: Conversation) -> int:
        request = self.context.get('request')
        if request is None:
            return 0
        last_read = None
        for p in obj.participants.all():
            if p.user_id == request.user.pk:
                last_read = p.last_read_at
                break
        qs = Message.objects.filter(conversation=obj, is_deleted=False)
        if last_read is not None:
            qs = qs.filter(created_at__gt=last_read)
        return qs.count()


class ConversationCreateSerializer(serializers.Serializer):
    """
    Payload for initiating a conversation. `conversation_type` selects which
    target/course fields are required:

      learner_instructor (default): course_id + instructor_id
      co_instructor              : course_id + peer_instructor_id
      institution_expert         : expert_user_id (course_id optional)
    """

    conversation_type = serializers.ChoiceField(
        choices=Conversation.ConversationType.choices,
        required=False,
        default=Conversation.ConversationType.LEARNER_INSTRUCTOR,
    )
    course_id = serializers.IntegerField(min_value=1, required=False)
    instructor_id = serializers.IntegerField(min_value=1, required=False)
    peer_instructor_id = serializers.IntegerField(min_value=1, required=False)
    expert_user_id = serializers.IntegerField(min_value=1, required=False)
    body = serializers.CharField(max_length=5000)

    def validate_body(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('Message body must not be blank.')
        return stripped

    def validate(self, attrs):
        ctype = attrs['conversation_type']
        CType = Conversation.ConversationType
        if ctype == CType.LEARNER_INSTRUCTOR:
            self._require(attrs, 'course_id', 'instructor_id')
        elif ctype == CType.CO_INSTRUCTOR:
            self._require(attrs, 'course_id', 'peer_instructor_id')
        elif ctype == CType.INSTITUTION_EXPERT:
            self._require(attrs, 'expert_user_id')
        return attrs

    @staticmethod
    def _require(attrs, *fields):
        missing = [f for f in fields if attrs.get(f) is None]
        if missing:
            raise serializers.ValidationError(
                {f: 'This field is required for this conversation type.' for f in missing}
            )
