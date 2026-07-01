from rest_framework import serializers

from courses.all_serializers.course_serializers import (
    CourseCategoryBriefSerializer,
    InstructorBriefSerializer,
    PartnerInstitutionBriefSerializer,
)
from webinars.models import Webinar, WebinarRegistration


class RegistrantWebinarSerializer(serializers.ModelSerializer):
    """
    Webinar summary for a registrant. Unlike the catalog serializers, this DOES
    expose ``meeting_url`` — the caller has registered, so they get the join
    link. Only ever nested inside registrant-facing payloads.
    """

    partner_institution = PartnerInstitutionBriefSerializer(read_only=True, allow_null=True)
    category = CourseCategoryBriefSerializer(read_only=True)
    host_expert = InstructorBriefSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Webinar
        fields = [
            'id', 'title', 'slug', 'description', 'thumbnail',
            'scheduled_at', 'timezone', 'duration_minutes', 'price',
            'meeting_provider', 'meeting_url',
            'partner_institution', 'category', 'host_expert', 'guest_speakers',
        ]
        read_only_fields = fields


class WebinarRegistrationSerializer(serializers.ModelSerializer):
    webinar = RegistrantWebinarSerializer(read_only=True)

    class Meta:
        model = WebinarRegistration
        fields = [
            'id', 'webinar', 'is_active', 'attended', 'joined_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields
