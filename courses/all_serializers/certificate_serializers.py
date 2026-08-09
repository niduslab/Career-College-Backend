from django.urls import reverse
from rest_framework import serializers

from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse


class CertificateSerializer(serializers.ModelSerializer):
    """Authenticated learner — own certificate metadata."""

    class Meta:
        model = Certificate
        fields = ['certificate_uid', 'learner_name', 'course_title', 'issued_at']


class PublicCertificateSerializer(serializers.ModelSerializer):
    """Public verification — same fields plus is_valid flag."""

    is_valid = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = ['certificate_uid', 'learner_name', 'course_title', 'issued_at', 'is_valid']

    def get_is_valid(self, obj) -> bool:
        return True


class _CertificateCourseBriefSerializer(serializers.ModelSerializer):
    """Live course card fields for the certificate row."""

    class Meta:
        model = NidusCourse
        fields = ['id', 'title', 'slug', 'thumbnail']
        read_only_fields = fields


class LearnerCertificateListSerializer(serializers.ModelSerializer):
    """Row for GET /my-certificates/ — frozen snapshot + live course + URLs.

    Both `course_title` (the snapshot frozen at issue, the legal record) and
    `course.title` (live) are returned; they can legitimately differ after a
    course is renamed. URLs are relative, matching the catalog's relative
    thumbnails — the frontend already prefixes the API base.
    """

    course = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    verify_url = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = [
            'certificate_uid', 'learner_name', 'course_title', 'issued_at',
            'course', 'download_url', 'verify_url',
        ]
        read_only_fields = fields

    def get_course(self, obj):
        return _CertificateCourseBriefSerializer(obj.enrollment.course).data

    def get_download_url(self, obj):
        return reverse(
            'courses:certificate-download',
            kwargs={'certificate_uid': str(obj.certificate_uid)},
        )

    def get_verify_url(self, obj):
        return reverse(
            'courses:certificate-verify',
            kwargs={'certificate_uid': str(obj.certificate_uid)},
        )
