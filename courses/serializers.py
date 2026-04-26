from auth.models import PartnerInstitutionProfile, User
from rest_framework import serializers

from courses.models import (
    CourseAudience,
    CourseCategory,
    CourseLearningObjective,
    CoursePreRequisite,
    CourseSection,
    Lecture,
    NidusCourse,
    VideoAsset,
)


class InstructorBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'full_name', 'email']
        read_only_fields = fields


class PartnerInstitutionBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerInstitutionProfile
        fields = ['id', 'institution_name', 'slug']
        read_only_fields = fields


class CourseCategoryBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseCategory
        fields = ['id', 'name', 'slug']
        read_only_fields = fields


class CourseLearningObjectiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseLearningObjective
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']


class CoursePreRequisiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoursePreRequisite
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']


class CourseAudienceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseAudience
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']


class NidusCourseSerializer(serializers.ModelSerializer):
    created_by = InstructorBriefSerializer(read_only=True)
    instructors = InstructorBriefSerializer(read_only=True, many=True)
    partner_institutions = PartnerInstitutionBriefSerializer(read_only=True, many=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    learning_objectives = CourseLearningObjectiveSerializer(read_only=True, many=True)
    prerequisites = CoursePreRequisiteSerializer(read_only=True, many=True)
    audiences = CourseAudienceSerializer(read_only=True, many=True)

    class Meta:
        model = NidusCourse
        fields = [
            'id',
            'title',
            'slug',
            'description',
            'thumbnail',
            'price',
            'language',
            'level',
            'duration_minutes',
            'status',
            'is_published',
            'rejection_reason',
            'published_at',
            'created_by',
            'instructors',
            'partner_institutions',
            'category',
            'learning_objectives',
            'prerequisites',
            'audiences',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class NidusCourseCreateUpdateSerializer(serializers.ModelSerializer):
    instructors = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.filter(user_type='instructor', is_deleted=False),
        required=False,
    )
    partner_institutions = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=PartnerInstitutionProfile.objects.filter(is_active=True),
        required=False,
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=CourseCategory.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    learning_objectives = CourseLearningObjectiveSerializer(many=True, required=False)
    prerequisites = CoursePreRequisiteSerializer(many=True, required=False)
    audiences = CourseAudienceSerializer(many=True, required=False)

    class Meta:
        model = NidusCourse
        fields = [
            'title',
            'description',
            'thumbnail',
            'price',
            'language',
            'level',
            'duration_minutes',
            'status',
            'rejection_reason',
            'instructors',
            'partner_institutions',
            'category',
            'learning_objectives',
            'prerequisites',
            'audiences',
        ]

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 5:
            raise serializers.ValidationError('Title must be at least 5 characters long.')
        return title

    def validate(self, attrs):
        status_value = attrs.get('status')
        rejection_reason = attrs.get('rejection_reason', '')
        if status_value == NidusCourse.CourseStatus.REJECTED and not rejection_reason.strip():
            raise serializers.ValidationError({'rejection_reason': 'Rejection reason is required for rejected status.'})
        return attrs

    def _replace_items(self, model_class, course, items):
        model_class.objects.filter(course=course).delete()
        new_objects = [
            model_class(
                course=course,
                text=item['text'].strip(),
                display_order=item.get('display_order', index),
            )
            for index, item in enumerate(items)
            if item.get('text', '').strip()
        ]
        if new_objects:
            model_class.objects.bulk_create(new_objects)

    def create(self, validated_data):
        learning_objectives = validated_data.pop('learning_objectives', [])
        prerequisites = validated_data.pop('prerequisites', [])
        audiences = validated_data.pop('audiences', [])
        instructors = validated_data.pop('instructors', [])
        partner_institutions = validated_data.pop('partner_institutions', [])

        request_user = self.context['request'].user
        course = NidusCourse.objects.create(created_by=request_user, **validated_data)

        if not instructors:
            instructors = [request_user]
        elif request_user not in instructors:
            instructors.append(request_user)

        course.instructors.set(instructors)
        course.partner_institutions.set(partner_institutions)
        self._replace_items(CourseLearningObjective, course, learning_objectives)
        self._replace_items(CoursePreRequisite, course, prerequisites)
        self._replace_items(CourseAudience, course, audiences)
        return course

    def update(self, instance, validated_data):
        learning_objectives = validated_data.pop('learning_objectives', None)
        prerequisites = validated_data.pop('prerequisites', None)
        audiences = validated_data.pop('audiences', None)
        instructors = validated_data.pop('instructors', None)
        partner_institutions = validated_data.pop('partner_institutions', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        request_user = self.context['request'].user

        if instructors is not None:
            if request_user not in instructors:
                instructors.append(request_user)
            instance.instructors.set(instructors)

        if partner_institutions is not None:
            instance.partner_institutions.set(partner_institutions)

        if learning_objectives is not None:
            self._replace_items(CourseLearningObjective, instance, learning_objectives)
        if prerequisites is not None:
            self._replace_items(CoursePreRequisite, instance, prerequisites)
        if audiences is not None:
            self._replace_items(CourseAudience, instance, audiences)

        return instance


class VideoAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoAsset
        fields = [
            'id',
            'video_file',
            'original_filename',
            'mime_type',
            'file_size',
            'duration_seconds',
            'master_playlist',
            'renditions',
            'is_active',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class CourseSectionSerializer(serializers.ModelSerializer):
    course_id = serializers.IntegerField(source='course.id', read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)

    class Meta:
        model = CourseSection
        fields = [
            'id',
            'course_id',
            'course_title',
            'title',
            'description',
            'position',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'course_id', 'course_title', 'created_at', 'updated_at']


class CourseSectionCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseSection
        fields = ['title', 'description', 'position']

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Section title must be at least 2 characters long.')
        return title


class LectureSerializer(serializers.ModelSerializer):
    section_id = serializers.IntegerField(source='section.id', read_only=True)
    active_video_asset = serializers.SerializerMethodField()

    class Meta:
        model = Lecture
        fields = [
            'id',
            'section_id',
            'title',
            'position',
            'content_type',
            'article_content',
            'stream_master_playlist',
            'stream_renditions',
            'transcoding_error',
            'active_video_asset',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_active_video_asset(self, obj):
        asset = obj.video_assets.filter(is_active=True).order_by('-created_at').first()
        if not asset:
            return None
        return VideoAssetSerializer(asset).data


class LectureCreateUpdateSerializer(serializers.ModelSerializer):
    video_file = serializers.FileField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Lecture
        fields = [
            'title',
            'position',
            'content_type',
            'article_content',
            'video_file',
        ]

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 2:
            raise serializers.ValidationError('Lecture title must be at least 2 characters long.')
        return title

    def validate(self, attrs):
        content_type = attrs.get('content_type')
        article_content = attrs.get('article_content')
        video_file = attrs.get('video_file')

        if self.instance is not None and content_type is None:
            content_type = self.instance.content_type
        if self.instance is not None and article_content is None:
            article_content = self.instance.article_content

        if content_type == Lecture.ContentType.ARTICLE:
            if video_file:
                raise serializers.ValidationError({'video_file': 'Article lectures cannot include video files.'})
            if not (article_content or '').strip():
                raise serializers.ValidationError({'article_content': 'Article lectures require content.'})

        if content_type == Lecture.ContentType.VIDEO:
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
