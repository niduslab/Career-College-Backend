"""
Learner-facing serializers for the Phase-1 consumption surface.

Kept separate from the instructor-side serializers so sensitive fields
(model_answer, solution_code, hidden test cases, quiz correctness flags)
cannot accidentally bleed into a learner response — these serializers
simply do not declare those fields.
"""

from rest_framework import serializers

from courses.all_serializers.content_serializers import (
    _normalize_media_relative_path,
    _normalize_renditions_playlists,
)


class LearnerWatchProgressSerializer(serializers.Serializer):
    """Read-side projection of a learner's per-lecture progress."""

    watched_seconds = serializers.IntegerField()
    is_completed = serializers.BooleanField()
    last_watched_at = serializers.DateTimeField(allow_null=True)


class LearnerLectureDetailSerializer(serializers.Serializer):
    """
    Learner-safe lecture payload.

    Video lectures expose HLS playlist + renditions; article lectures expose
    the article text. `transcoding_error` and the raw `VideoAsset` (including
    file paths and mime type) are deliberately omitted.
    """

    id = serializers.IntegerField()
    section_id = serializers.IntegerField()
    title = serializers.CharField()
    lecture_type = serializers.CharField()
    article_content = serializers.CharField()
    stream_master_playlist = serializers.SerializerMethodField()
    stream_renditions = serializers.SerializerMethodField()
    duration_seconds = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    def get_stream_master_playlist(self, lecture):
        return _normalize_media_relative_path(lecture.stream_master_playlist)

    def get_stream_renditions(self, lecture):
        return _normalize_renditions_playlists(lecture.stream_renditions)

    def get_duration_seconds(self, lecture):
        # Pulled from the active VideoAsset via the view's context; falls back
        # to None for article lectures or videos that haven't finished probing.
        return self.context.get('duration_seconds')

    def get_progress(self, lecture):
        wp = self.context.get('watch_progress')
        if wp is None:
            return None
        return {
            'watched_seconds': wp.watched_seconds,
            'is_completed': wp.is_completed,
            'last_watched_at': wp.last_watched_at,
        }


class WatchProgressUpsertSerializer(serializers.Serializer):
    """Validate the POST body for `/learn/lectures/<id>/progress/`."""

    watched_seconds = serializers.IntegerField(min_value=0)
    is_completed = serializers.BooleanField()
