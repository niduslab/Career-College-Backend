from rest_framework import serializers

from courses.all_serializers.course_serializers import InstructorBriefSerializer
from courses.models import (
    CourseSection,
    Lecture,
    SectionContent,
    VideoAsset,
)


def _normalize_media_relative_path(path_value):
    if not isinstance(path_value, str):
        return path_value
    normalized = path_value.replace('\\', '/').lstrip('/')
    if normalized.startswith('media/'):
        return normalized[len('media/'):]
    return normalized


def _normalize_renditions_playlists(renditions):
    if not isinstance(renditions, list):
        return renditions
    normalized_renditions = []
    for item in renditions:
        if not isinstance(item, dict):
            normalized_renditions.append(item)
            continue
        row = dict(item)
        row['playlist'] = _normalize_media_relative_path(row.get('playlist', ''))
        normalized_renditions.append(row)
    return normalized_renditions
class VideoAssetSerializer(serializers.ModelSerializer):
    master_playlist = serializers.SerializerMethodField()
    renditions = serializers.SerializerMethodField()

    class Meta:
        model = VideoAsset
        fields = [
            'id', 'video_file', 'original_filename', 'mime_type', 'file_size',
            'duration_seconds', 'master_playlist', 'renditions', 'is_active',
            'status', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_master_playlist(self, obj):
        return _normalize_media_relative_path(obj.master_playlist)

    def get_renditions(self, obj):
        return _normalize_renditions_playlists(obj.renditions)


# ---------------------------------------------------------------------------
# Section serializers
# ---------------------------------------------------------------------------

class CourseSectionSerializer(serializers.ModelSerializer):
    course_id = serializers.IntegerField(read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)
    created_by = InstructorBriefSerializer(read_only=True)
    last_edited_by = InstructorBriefSerializer(read_only=True)

    class Meta:
        model = CourseSection
        fields = [
            'id', 'course_id', 'course_title', 'title', 'description',
            'position', 'unlocks_at', 'created_by', 'last_edited_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'course_id', 'course_title', 'created_by', 'last_edited_by', 'created_at', 'updated_at']


class CourseSectionCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseSection
        fields = ['title', 'description', 'position', 'unlocks_at']

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Section title must be at least 2 characters long.')
        return title


# ---------------------------------------------------------------------------
# Lecture serializers — position field removed
# ---------------------------------------------------------------------------

class LectureSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(read_only=True)
    stream_master_playlist = serializers.SerializerMethodField()
    stream_renditions = serializers.SerializerMethodField()
    active_video_asset = serializers.SerializerMethodField()
    created_by = InstructorBriefSerializer(read_only=True)
    last_edited_by = InstructorBriefSerializer(read_only=True)

    class Meta:
        model = Lecture
        fields = [
            'id', 'section_id', 'title',
            'lecture_type', 'article_content', 'is_preview',
            'stream_master_playlist', 'stream_renditions', 'transcoding_error',
            'active_video_asset', 'created_by', 'last_edited_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_stream_master_playlist(self, obj):
        return _normalize_media_relative_path(obj.stream_master_playlist)

    def get_stream_renditions(self, obj):
        return _normalize_renditions_playlists(obj.stream_renditions)

    def get_active_video_asset(self, obj):
        # Iterate the prefetched list so list endpoints don't N+1.
        # `.filter()` after `.prefetch_related('video_assets')` would discard the cache.
        actives = [a for a in obj.video_assets.all() if a.is_active]
        if not actives:
            return None
        actives.sort(key=lambda a: a.created_at, reverse=True)
        return VideoAssetSerializer(actives[0]).data


class LectureCreateUpdateSerializer(serializers.ModelSerializer):
    video_file = serializers.FileField(write_only=True, required=False, allow_null=True)
    lecture_type = serializers.ChoiceField(choices=Lecture.LectureType.choices, required=True)

    class Meta:
        model = Lecture
        fields = ['title', 'lecture_type', 'article_content', 'is_preview', 'video_file']

    def to_internal_value(self, data):
        unknown_fields = set(data.keys()) - set(self.fields.keys())
        if unknown_fields:
            raise serializers.ValidationError(
                {field: 'Unknown field.' for field in sorted(unknown_fields)}
            )
        return super().to_internal_value(data)

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Lecture title must be at least 2 characters long.')
        return title

    def validate(self, attrs):
        lecture_type = attrs.get('lecture_type')
        article_content = attrs.get('article_content')
        video_file = attrs.get('video_file')

        if self.instance is not None and lecture_type is None:
            lecture_type = self.instance.lecture_type
        if self.instance is not None and article_content is None:
            article_content = self.instance.article_content

        if lecture_type == Lecture.LectureType.ARTICLE:
            if video_file:
                raise serializers.ValidationError({'video_file': 'Article lectures cannot include video files.'})
            if not (article_content or '').strip():
                raise serializers.ValidationError({'article_content': 'Article lectures require content.'})

        if lecture_type == Lecture.LectureType.VIDEO:
            if (article_content or '').strip():
                raise serializers.ValidationError({'article_content': 'Video lectures cannot include article content.'})
            creating = self.instance is None
            if creating and not video_file:
                raise serializers.ValidationError({'video_file': 'Video lectures require a video file on creation.'})

        return attrs

    def create(self, validated_data):
        video_file = validated_data.pop('video_file', None)
        section = self.context['section']
        lecture = Lecture.objects.create(section=section, **validated_data)
        if video_file:
            from courses.services import replace_lecture_video_and_enqueue_transcoding
            replace_lecture_video_and_enqueue_transcoding(lecture=lecture, uploaded_file=video_file)
        return lecture

    def update(self, instance, validated_data):
        video_file = validated_data.pop('video_file', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if video_file:
            from courses.services import replace_lecture_video_and_enqueue_transcoding
            replace_lecture_video_and_enqueue_transcoding(lecture=instance, uploaded_file=video_file)
        return instance


# ---------------------------------------------------------------------------
# SectionContent serializer
# Bulk-loading maps (lectures, quizzes) are passed via serializer context
# by the view to avoid N+1 queries.
# ---------------------------------------------------------------------------

class SectionContentSerializer(serializers.ModelSerializer):
    content = serializers.SerializerMethodField()
    created_by = InstructorBriefSerializer(read_only=True)
    last_edited_by = InstructorBriefSerializer(read_only=True)

    class Meta:
        model = SectionContent
        fields = [
            'id', 'section', 'item_type', 'object_id', 'position', 'content',
            'created_by', 'last_edited_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_content(self, obj):
        lectures: dict = self.context.get('lectures', {})
        quizzes: dict = self.context.get('quizzes', {})
        coding_exercises: dict = self.context.get('coding_exercises', {})
        assignments: dict = self.context.get('assignments', {})

        if obj.item_type == SectionContent.ItemType.LECTURE:
            lecture = lectures.get(obj.object_id)
            if lecture:
                actives = [a for a in lecture.video_assets.all() if a.is_active]
                active_video_asset = None
                if actives:
                    actives.sort(key=lambda a: a.created_at, reverse=True)
                    active_video_asset = VideoAssetSerializer(actives[0]).data
                return {
                    'id': lecture.id,
                    'title': lecture.title,
                    'lecture_type': lecture.lecture_type,
                    'is_preview': lecture.is_preview,
                    'active_video_asset': active_video_asset,
                }

        elif obj.item_type == SectionContent.ItemType.QUIZ:
            quiz = quizzes.get(obj.object_id)
            if quiz:
                return {'id': quiz.id, 'title': quiz.title}

        elif obj.item_type == SectionContent.ItemType.CODING:
            ex = coding_exercises.get(obj.object_id)
            if ex:
                return {
                    'id': ex.id,
                    'title': ex.title,
                    'language': ex.language,
                }

        elif obj.item_type == SectionContent.ItemType.ASSIGNMENT:
            assignment = assignments.get(obj.object_id)
            if assignment:
                return {
                    'id': assignment.id,
                    'title': assignment.title,
                    'passing_score': assignment.passing_score,
                }

        return None

