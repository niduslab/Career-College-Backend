from rest_framework import serializers


class _CourseRefSerializer(serializers.Serializer):
    """Course card stub shared by every dashboard payload."""

    id = serializers.IntegerField()
    title = serializers.CharField()
    slug = serializers.CharField()
    thumbnail = serializers.CharField(allow_null=True)


class LearnerSummarySerializer(serializers.Serializer):
    """KPI tiles.

    Deliberately has no `total_xp` field — see `get_learner_summary` for why.
    `day_streak_is_approximate` and `day_streak_timezone` let the UI qualify
    the streak instead of presenting a derived number as exact.
    """

    courses_enrolled = serializers.IntegerField()
    courses_in_progress = serializers.IntegerField()
    courses_completed = serializers.IntegerField()
    certificates_earned = serializers.IntegerField()
    average_progress_percent = serializers.FloatField()
    total_learning_seconds = serializers.IntegerField()
    total_learning_hours = serializers.FloatField()
    lectures_completed = serializers.IntegerField()
    day_streak = serializers.IntegerField()
    day_streak_is_approximate = serializers.BooleanField()
    day_streak_timezone = serializers.CharField()


class LearnerActivityItemSerializer(serializers.Serializer):
    """One row of the merged activity feed.

    `id` is a `"<source>:<pk>"` composite so React has a stable key across a
    heterogeneous list. `meta` is type-specific — see the per-source builders.
    """

    id = serializers.CharField()
    type = serializers.CharField()
    occurred_at = serializers.DateTimeField()
    title = serializers.CharField()
    course = _CourseRefSerializer(allow_null=True)
    meta = serializers.DictField()


class _WebinarRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    slug = serializers.CharField()


class LearnerUpcomingItemSerializer(serializers.Serializer):
    """One upcoming date. Exactly one of `course` / `webinar` is populated."""

    type = serializers.CharField()
    occurs_at = serializers.DateTimeField()
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_null=True)
    course = _CourseRefSerializer(allow_null=True)
    webinar = _WebinarRefSerializer(allow_null=True)
    meta = serializers.DictField()


class LearnerUpcomingSerializer(serializers.Serializer):
    horizon_days = serializers.IntegerField()
    count = serializers.IntegerField()
    items = LearnerUpcomingItemSerializer(many=True)


class _ContinueEnrollmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    progress_percent = serializers.IntegerField()
    last_accessed_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class _ContinueSectionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    position = serializers.IntegerField()


class _ContinueNextLectureSerializer(serializers.Serializer):
    lecture_id = serializers.IntegerField()
    content_id = serializers.IntegerField()
    title = serializers.CharField()
    lecture_type = serializers.CharField()
    duration_seconds = serializers.IntegerField(allow_null=True)
    section = _ContinueSectionSerializer()


class LearnerContinueSerializer(serializers.Serializer):
    """Resume target.

    `next_lecture` is null when the course is finished or when everything left
    is still locked — `locked_until` distinguishes the two.
    """

    enrollment = _ContinueEnrollmentSerializer()
    course = _CourseRefSerializer()
    next_lecture = _ContinueNextLectureSerializer(allow_null=True)
    is_course_complete = serializers.BooleanField()
    locked_until = serializers.DateTimeField(allow_null=True)
