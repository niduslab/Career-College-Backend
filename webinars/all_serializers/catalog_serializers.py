from rest_framework import serializers

from courses.all_serializers.course_serializers import (
    CourseCategoryBriefSerializer,
    InstructorBriefSerializer,
    PartnerInstitutionBriefSerializer,
)
from webinars.models import Webinar


class CatalogWebinarListSerializer(serializers.ModelSerializer):
    """
    Public list serializer. Deliberately declares NO ``meeting_url`` — the join
    link is registrant-only. Absence is a stronger guarantee than conditional
    removal.
    """

    partner_institution = PartnerInstitutionBriefSerializer(read_only=True, allow_null=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    host_expert = InstructorBriefSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Webinar
        fields = [
            'id', 'title', 'slug', 'thumbnail', 'scheduled_at', 'timezone',
            'duration_minutes', 'max_capacity', 'price',
            'partner_institution', 'category', 'host_expert',
        ]
        read_only_fields = fields


class CatalogWebinarDetailSerializer(serializers.ModelSerializer):
    """Public detail serializer. No ``meeting_url`` (registrant-only)."""

    partner_institution = PartnerInstitutionBriefSerializer(read_only=True, allow_null=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    host_expert = InstructorBriefSerializer(read_only=True, allow_null=True)
    institutional_speakers = InstructorBriefSerializer(many=True, read_only=True)

    class Meta:
        model = Webinar
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail',
            'scheduled_at', 'timezone', 'duration_minutes', 'max_capacity',
            'price', 'partner_institution', 'category',
            'host_expert', 'institutional_speakers', 'guest_speakers', 'published_at',
        ]
        read_only_fields = fields
