from rest_framework import serializers

from courses.all_models.review_models import CourseReview


class CourseReviewReadSerializer(serializers.ModelSerializer):
    """Read-only serializer for a published review, including the caller's vote."""

    reviewer_name = serializers.CharField(source='user.full_name', read_only=True)
    viewer_vote = serializers.SerializerMethodField()

    class Meta:
        model = CourseReview
        fields = [
            'id',
            'rating',
            'headline',
            'body',
            'helpful_count',
            'not_helpful_count',
            'reviewer_name',
            'viewer_vote',
            'created_at',
            'updated_at',
        ]

    def get_viewer_vote(self, obj):
        """Return 'helpful', 'not_helpful', or None for the requesting user.

        Reads from the `_viewer_vote` annotation added by the view's queryset
        when the caller is authenticated. Falls back to None when absent.
        """
        vote = getattr(obj, '_viewer_vote', None)
        if vote is None:
            return None
        return 'helpful' if vote else 'not_helpful'


class CourseReviewWriteSerializer(serializers.Serializer):
    """Validates the payload for creating or updating a review."""

    rating = serializers.IntegerField(min_value=1, max_value=5)
    headline = serializers.CharField(max_length=150)
    body = serializers.CharField(allow_blank=True, required=False, default='')

    def validate_headline(self, value):
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError('Headline must not be blank.')
        return stripped

    def validate_body(self, value):
        return value.strip() if value else ''


class CourseReviewSummarySerializer(serializers.Serializer):
    """Aggregated rating stats for a course — no model instance needed."""

    avg_rating = serializers.FloatField()
    review_count = serializers.IntegerField()
    distribution = serializers.DictField(child=serializers.IntegerField())


class ReviewVoteSerializer(serializers.Serializer):
    """Payload for casting or flipping a review vote."""

    is_helpful = serializers.BooleanField()
