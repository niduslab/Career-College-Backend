from django.db import transaction
from rest_framework import serializers

from authentication.models import PartnerInstitutionProfile, User
from courses.models import (
    CourseAudience,
    CourseCategory,
    CourseLearningObjective,
    CoursePreRequisite,
    NidusCourse,
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


# ---------------------------------------------------------------------------
# Course item serializers (unchanged)
# ---------------------------------------------------------------------------

class CourseLearningObjectiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseLearningObjective
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']

    def validate_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Text cannot be empty.')
        return text


class CoursePreRequisiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoursePreRequisite
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']

    def validate_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Text cannot be empty.')
        return text


class CourseAudienceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseAudience
        fields = ['id', 'text', 'display_order']
        read_only_fields = ['id']

    def validate_text(self, value):
        text = value.strip()
        if not text:
            raise serializers.ValidationError('Text cannot be empty.')
        return text

class NidusCourseSerializer(serializers.ModelSerializer):
    created_by = InstructorBriefSerializer(read_only=True)
    instructors = InstructorBriefSerializer(read_only=True, many=True)
    partner_institution = PartnerInstitutionBriefSerializer(read_only=True, allow_null=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    learning_objectives = CourseLearningObjectiveSerializer(read_only=True, many=True)
    prerequisites = CoursePreRequisiteSerializer(read_only=True, many=True)
    audiences = CourseAudienceSerializer(read_only=True, many=True)

    class Meta:
        model = NidusCourse
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail', 'price',
            'language', 'level', 'duration_minutes', 'status', 'is_published',
            'rejection_reason', 'published_at', 'created_by', 'instructors',
            'partner_institution', 'category', 'learning_objectives',
            'prerequisites', 'audiences', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class NidusCourseCreateUpdateSerializer(serializers.ModelSerializer):
    instructors = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=User.objects.filter(user_type='instructor', is_deleted=False),
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
            'title', 'description', 'thumbnail', 'price', 'language', 'level',
            'duration_minutes', 'instructors', 'category', 'learning_objectives',
            'prerequisites', 'audiences',
        ]
        read_only_fields = ['created_by']

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 5:
            raise serializers.ValidationError('Title must be at least 5 characters long.')
        return title

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
        with transaction.atomic():
            learning_objectives = validated_data.pop('learning_objectives', [])
            prerequisites = validated_data.pop('prerequisites', [])
            audiences = validated_data.pop('audiences', [])
            instructors = validated_data.pop('instructors', [])

            request_user = self.context['request'].user
            course = NidusCourse.objects.create(created_by=request_user, **validated_data)

            if request_user.user_type == 'partner_institution':
                # auto-set FK; partner institution user is not added to the instructors M2M
                partner_profile = PartnerInstitutionProfile.objects.get(user=request_user)
                course.partner_institution = partner_profile
                course.save(update_fields=['partner_institution'])
            else:
                # instructor: ensure the creator is always in the M2M
                if not instructors:
                    instructors = [request_user]
                elif request_user not in instructors:
                    instructors.append(request_user)

            course.instructors.set(instructors)
            self._replace_items(CourseLearningObjective, course, learning_objectives)
            self._replace_items(CoursePreRequisite, course, prerequisites)
            self._replace_items(CourseAudience, course, audiences)
            return course

    def update(self, instance, validated_data):
        with transaction.atomic():
            learning_objectives = validated_data.pop('learning_objectives', None)
            prerequisites = validated_data.pop('prerequisites', None)
            audiences = validated_data.pop('audiences', None)
            instructors = validated_data.pop('instructors', None)

            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()

            request_user = self.context['request'].user

            if instructors is not None:
                if request_user == instance.created_by:
                    # instructor owner: keep themselves in the M2M
                    # partner institution owner: not in instructors M2M, don't add them
                    if request_user.user_type == 'instructor' and request_user not in instructors:
                        instructors.append(request_user)
                    instance.instructors.set(instructors)
                # co-instructors: silently ignore — roster is owner-only

            if learning_objectives is not None:
                self._replace_items(CourseLearningObjective, instance, learning_objectives)
            if prerequisites is not None:
                self._replace_items(CoursePreRequisite, instance, prerequisites)
            if audiences is not None:
                self._replace_items(CourseAudience, instance, audiences)

            return instance
