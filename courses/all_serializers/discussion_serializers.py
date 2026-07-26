from rest_framework import serializers

from courses.all_models.discussion_models import CourseQuestion, QuestionReply


class QuestionReplyReadSerializer(serializers.ModelSerializer):
    """A single reply, with author name, instructor badge and viewer upvote."""

    author_name = serializers.CharField(source='author.full_name', read_only=True)
    is_own = serializers.SerializerMethodField()

    class Meta:
        model = QuestionReply
        fields = [
            'id',
            'body',
            'author_name',
            'is_instructor_reply',
            'is_own',
            'upvote_count',
            'created_at',
            'updated_at',
        ]

    def get_is_own(self, obj):
        request = self.context.get('request')
        return bool(request and request.user.is_authenticated and obj.author_id == request.user.pk)


class _RelatedContentBriefSerializer(serializers.Serializer):
    """Minimal descriptor of the content item a question is anchored to."""

    id = serializers.IntegerField()
    item_type = serializers.CharField()


class CourseQuestionListSerializer(serializers.ModelSerializer):
    """List row for a question — no reply bodies, just counts."""

    author_name = serializers.CharField(source='author.full_name', read_only=True)
    related_content = _RelatedContentBriefSerializer(read_only=True)
    is_own = serializers.SerializerMethodField()

    class Meta:
        model = CourseQuestion
        fields = [
            'id',
            'title',
            'body',
            'author_name',
            'related_content',
            'is_pinned',
            'reply_count',
            'upvote_count',
            'is_own',
            'created_at',
            'updated_at',
        ]

    def get_is_own(self, obj):
        request = self.context.get('request')
        return bool(request and request.user.is_authenticated and obj.author_id == request.user.pk)


class CourseQuestionDetailSerializer(CourseQuestionListSerializer):
    """Question detail — same as the list row plus the nested replies."""

    replies = serializers.SerializerMethodField()

    class Meta(CourseQuestionListSerializer.Meta):
        fields = CourseQuestionListSerializer.Meta.fields + ['replies']

    def get_replies(self, obj):
        # `obj.replies` is prefetched (non-deleted, author-joined) by the service.
        return QuestionReplyReadSerializer(
            obj.replies.all(), many=True, context=self.context
        ).data


class CourseQuestionWriteSerializer(serializers.Serializer):
    """Validates the payload for creating a question."""

    title = serializers.CharField(max_length=255)
    body = serializers.CharField()
    related_content_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_title(self, value):
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('Title must not be blank.')
        return stripped

    def validate_body(self, value):
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('Body must not be blank.')
        return stripped


class QuestionReplyWriteSerializer(serializers.Serializer):
    """Validates the payload for posting a reply."""

    body = serializers.CharField()

    def validate_body(self, value):
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('Reply must not be blank.')
        return stripped
