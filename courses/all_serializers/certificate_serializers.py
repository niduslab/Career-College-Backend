from django.urls import reverse
from rest_framework import serializers

from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse
from courses.services.certificate_service import build_verification_url


def _image_url(field):
    """Relative media URL, or None. Matches the project's relative-URL convention."""
    if not field:
        return None
    try:
        return field.url
    except ValueError:
        return None


class CertificateSerializer(serializers.ModelSerializer):
    """Authenticated learner — own certificate metadata."""

    verification_url = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = [
            'certificate_uid', 'certificate_id', 'status',
            'learner_name', 'course_title', 'issued_at', 'verification_url',
        ]

    def get_verification_url(self, obj) -> str:
        return build_verification_url(obj)


class PublicCertificateSerializer(serializers.Serializer):
    """Public verification payload.

    Reads entirely from the certificate's frozen snapshot, never from the live
    course/profile rows — that is what makes a verified certificate stable.
    Deliberately excludes the learner's email and every enrollment internal.
    """

    certificate_id = serializers.CharField()
    certificate_uid = serializers.UUIDField()
    status = serializers.CharField()

    student = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()
    completion_date = serializers.DateField()
    issue_date = serializers.DateTimeField(source='issued_at')
    instructor = serializers.SerializerMethodField()
    authorized_signatory = serializers.SerializerMethodField()
    issuer = serializers.SerializerMethodField()
    verification_url = serializers.SerializerMethodField()
    revoked_at = serializers.DateTimeField(allow_null=True)

    def get_student(self, obj):
        return {'name': obj.learner_name}

    def get_course(self, obj):
        return {
            'name': obj.course_title,
            'duration': obj.course_duration,
            'learning_hours': obj.learning_hours,
        }

    def get_instructor(self, obj):
        return {
            'name': obj.instructor_name,
            'designation': obj.instructor_designation,
            'signature_url': _image_url(obj.instructor_signature),
        }

    def get_authorized_signatory(self, obj):
        return {
            'name': obj.authorized_signatory_name,
            'designation': obj.authorized_signatory_designation,
            'signature_url': _image_url(obj.authorized_signature),
        }

    def get_issuer(self, obj):
        return {'name': obj.issuer_name}

    def get_verification_url(self, obj) -> str:
        return build_verification_url(obj)


class AdminCertificateListSerializer(serializers.ModelSerializer):
    """Row for the admin console's certificate browser.

    Carries the revocation fields the learner-facing serializers omit, so an
    admin can see why and when a credential was revoked. Still reads from the
    frozen snapshot — never the live course or profile rows.
    """

    course = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = [
            'certificate_uid', 'certificate_id', 'status',
            'learner_name', 'course_title', 'issued_at', 'completion_date',
            'revoked_at', 'revoked_reason', 'issuer_name', 'course',
        ]
        read_only_fields = fields

    def get_course(self, obj):
        course = obj.enrollment.course
        return {'id': course.id, 'title': course.title, 'slug': course.slug}


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
    course is renamed. `download_url`/`verify_url` are relative API paths,
    matching the catalog's relative thumbnails — the frontend already prefixes
    the API base. `verification_url` is the separate absolute, frontend-facing
    URL the QR code encodes.
    """

    course = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    verify_url = serializers.SerializerMethodField()
    verification_url = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = [
            'certificate_uid', 'certificate_id', 'status',
            'learner_name', 'course_title', 'issued_at',
            'course', 'download_url', 'verify_url', 'verification_url',
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

    def get_verification_url(self, obj) -> str:
        return build_verification_url(obj)
