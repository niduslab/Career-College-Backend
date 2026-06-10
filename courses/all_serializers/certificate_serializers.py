from rest_framework import serializers

from courses.all_models.certificate_models import Certificate


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
