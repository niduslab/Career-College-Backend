from django.db import transaction
from rest_framework import serializers

from authentication.models import PartnerInstitutionProfile, User
from courses.models import (
    CourseCategory,
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


class CourseCategoryLeafSerializer(serializers.ModelSerializer):
    """Read serializer for a second-level category.

    The tree is enforced to exactly two levels (see `CourseCategoryWriteSerializer.validate`),
    so a child can never have children of its own — `children` is hard-coded to `[]` rather than
    queried, keeping the shape symmetric with `CourseCategoryTreeSerializer` without recursing.
    """

    children = serializers.SerializerMethodField()

    class Meta:
        model = CourseCategory
        fields = ['id', 'name', 'slug', 'children']
        read_only_fields = fields

    def get_children(self, obj):
        return []


class CourseCategoryTreeSerializer(serializers.ModelSerializer):
    """Public read serializer — top-level categories with their active children nested."""

    children = serializers.SerializerMethodField()

    class Meta:
        model = CourseCategory
        fields = ['id', 'name', 'slug', 'children']
        read_only_fields = fields

    def get_children(self, obj):
        # `.all()` reads from the view's prefetch cache; any further
        # queryset-narrowing call here (e.g. `.filter()`) would bypass the
        # cache and issue a fresh query per parent.
        return CourseCategoryLeafSerializer(obj.children.all(), many=True).data


class CourseCategoryWriteSerializer(serializers.ModelSerializer):
    """Admin create/update + detail serializer. Slug is optional (model.save() fills it)."""

    parent = serializers.PrimaryKeyRelatedField(
        queryset=CourseCategory.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = CourseCategory
        fields = [
            'id', 'name', 'slug', 'parent',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'slug': {'required': False, 'allow_blank': True},
        }

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError('Name cannot be empty.')
        return name

    def validate(self, attrs):
        parent = attrs.get('parent')
        if parent is not None:
            # Enforce a 2-level tree: a parent must itself be top-level.
            if parent.parent_id is not None:
                raise serializers.ValidationError(
                    {'parent': 'Categories support only two levels; the parent must be a top-level category.'}
                )
            # Reject self-parenting on update.
            if self.instance is not None and parent.pk == self.instance.pk:
                raise serializers.ValidationError(
                    {'parent': 'A category cannot be its own parent.'}
                )
            # A category with its own children must stay top-level, or its
            # children would end up three levels deep.
            if self.instance is not None and self.instance.children.exists():
                raise serializers.ValidationError(
                    {'parent': 'This category has subcategories and cannot be made a child category itself.'}
                )
        return attrs


class NidusCourseSerializer(serializers.ModelSerializer):
    created_by = InstructorBriefSerializer(read_only=True)
    instructors = InstructorBriefSerializer(read_only=True, many=True)
    partner_institution = PartnerInstitutionBriefSerializer(read_only=True, allow_null=True)
    category = CourseCategoryBriefSerializer(read_only=True)

    class Meta:
        model = NidusCourse
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail', 'price',
            'language', 'level', 'duration_minutes', 'delivery_mode', 'status', 'is_published',
            'rejection_reason', 'published_at', 'created_by', 'instructors',
            'partner_institution', 'category', 'learning_objectives',
            'prerequisites', 'audiences', 'course_outline', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class NidusCourseCreateUpdateSerializer(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(
        queryset=CourseCategory.objects.filter(is_active=True),
        required=True,
        allow_null=False,
    )

    class Meta:
        model = NidusCourse
        fields = [
            'title', 'description', 'thumbnail', 'price', 'language', 'level',
            'duration_minutes', 'delivery_mode', 'category', 'learning_objectives',
            'prerequisites', 'audiences', 'course_outline',
        ]
        read_only_fields = ['created_by']
        extra_kwargs = {
            'learning_objectives': {'required': True, 'allow_blank': False},
            'prerequisites': {'required': True, 'allow_blank': False},
            'audiences': {'required': True, 'allow_blank': False},
            'price': {'required': True},
        }

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 5:
            raise serializers.ValidationError('Title must be at least 5 characters long.')
        return title

    def validate_delivery_mode(self, value):
        if self.instance is not None and value != self.instance.delivery_mode:
            raise serializers.ValidationError('delivery_mode cannot be changed after the course is created.')
        return value

    def _normalize_multiline(self, value):
        lines = [line.strip() for line in value.split('\n')]
        return '\n'.join(line for line in lines if line)

    def _normalize_required(self, value, label):
        normalized = self._normalize_multiline(value)
        if not normalized:
            raise serializers.ValidationError(f'{label} cannot be empty.')
        return normalized

    def validate_learning_objectives(self, value):
        return self._normalize_required(value, 'Learning objectives')

    def validate_prerequisites(self, value):
        return self._normalize_required(value, 'Prerequisites')

    def validate_audiences(self, value):
        return self._normalize_required(value, 'Audiences')

    def validate_course_outline(self, value):
        return self._normalize_multiline(value)

    def create(self, validated_data):
        with transaction.atomic():
            request_user = self.context['request'].user
            course = NidusCourse.objects.create(created_by=request_user, **validated_data)

            if request_user.user_type == 'partner_institution':
                partner_profile = PartnerInstitutionProfile.objects.get(user=request_user)
                course.partner_institution = partner_profile
                course.save(update_fields=['partner_institution'])
            else:
                course.instructors.set([request_user])

            return course

    def update(self, instance, validated_data):
        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            return instance
