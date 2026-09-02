from rest_framework import serializers

from admin_console.all_models.platform_settings_models import PlatformSettings


class PlatformSettingsSerializer(serializers.ModelSerializer):
    """Read + partial-update of the platform singleton.

    `authorized_signature` accepts a multipart file upload; the model's
    validators enforce the extension and the 2 MB size cap.
    """

    class Meta:
        model = PlatformSettings
        fields = [
            'organization_name',
            'authorized_signatory_name',
            'authorized_signatory_designation',
            'authorized_signature',
            'default_commission_pct',
            'updated_at',
        ]
        read_only_fields = ['updated_at']

    def validate_organization_name(self, value):
        cleaned = (value or '').strip()
        if not cleaned:
            raise serializers.ValidationError('Organization name cannot be blank.')
        return cleaned

    def validate_default_commission_pct(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError('Commission percentage must be between 0 and 100.')
        return value
