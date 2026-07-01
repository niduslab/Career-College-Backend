from django.db import transaction
from rest_framework import serializers

from authentication.models import PartnerInstitutionProfile
from courses.all_serializers.course_serializers import (
    CourseCategoryBriefSerializer,
    InstructorBriefSerializer,
    PartnerInstitutionBriefSerializer,
)
from courses.models import CourseCategory
from webinars.models import Webinar
from webinars.services import set_institutional_speakers


class GuestSpeakerSerializer(serializers.Serializer):
    """One external presenter with no platform account."""

    full_name = serializers.CharField(max_length=255)
    title = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    bio = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_full_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError('Guest speaker name cannot be empty.')
        return name


class WebinarSerializer(serializers.ModelSerializer):
    """Authoring read serializer — full detail incl. meeting_url + status."""

    created_by = InstructorBriefSerializer(read_only=True)
    last_edited_by = InstructorBriefSerializer(read_only=True)
    host_expert = InstructorBriefSerializer(read_only=True, allow_null=True)
    institutional_speakers = InstructorBriefSerializer(many=True, read_only=True)
    partner_institution = PartnerInstitutionBriefSerializer(read_only=True, allow_null=True)
    category = CourseCategoryBriefSerializer(read_only=True)

    class Meta:
        model = Webinar
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail',
            'scheduled_at', 'timezone', 'duration_minutes', 'max_capacity',
            'price', 'meeting_provider', 'meeting_url',
            'status', 'is_published', 'published_at',
            'host_expert', 'institutional_speakers', 'guest_speakers',
            'partner_institution', 'category',
            'created_by', 'last_edited_by', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class WebinarCreateUpdateSerializer(serializers.ModelSerializer):
    category = serializers.PrimaryKeyRelatedField(
        queryset=CourseCategory.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    guest_speakers = GuestSpeakerSerializer(many=True, required=False)
    institutional_speaker_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        write_only=True,
        help_text='User ids of active affiliated experts to credit as speakers. Replaces the existing set.',
    )

    class Meta:
        model = Webinar
        fields = [
            'title', 'description', 'thumbnail', 'scheduled_at', 'timezone',
            'duration_minutes', 'max_capacity', 'price', 'meeting_provider',
            'meeting_url', 'category', 'guest_speakers', 'institutional_speaker_ids',
        ]

    def validate_title(self, value):
        title = value.strip()
        if len(title) < 5:
            raise serializers.ValidationError('Title must be at least 5 characters long.')
        return title

    def create(self, validated_data):
        with transaction.atomic():
            guest_speakers = validated_data.pop('guest_speakers', [])
            speaker_ids = validated_data.pop('institutional_speaker_ids', None)
            request_user = self.context['request'].user
            partner_profile = PartnerInstitutionProfile.objects.get(user=request_user)

            webinar = Webinar.objects.create(
                created_by=request_user,
                last_edited_by=request_user,
                partner_institution=partner_profile,
                guest_speakers=[dict(g) for g in guest_speakers],
                **validated_data,
            )
            if speaker_ids is not None:
                set_institutional_speakers(webinar, partner_profile, speaker_ids)
            return webinar

    def update(self, instance, validated_data):
        with transaction.atomic():
            guest_speakers = validated_data.pop('guest_speakers', None)
            speaker_ids = validated_data.pop('institutional_speaker_ids', None)
            request_user = self.context['request'].user
            partner_profile = instance.partner_institution

            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            if guest_speakers is not None:
                instance.guest_speakers = [dict(g) for g in guest_speakers]
            instance.last_edited_by = request_user
            instance.save()
            if speaker_ids is not None:
                set_institutional_speakers(instance, partner_profile, speaker_ids)
            return instance
