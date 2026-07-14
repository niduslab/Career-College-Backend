from rest_framework import serializers

from admin_console.all_models import AdminSession


class AdminSessionSerializer(serializers.ModelSerializer):
    """Read-only view of one admin device/session for the 'your sessions' list."""

    is_current = serializers.SerializerMethodField()

    class Meta:
        model = AdminSession
        fields = [
            'id',
            'ip_address',
            'user_agent',
            'browser',
            'os',
            'device',
            'created_at',
            'last_seen_at',
            'is_current',
        ]
        read_only_fields = fields

    def get_is_current(self, obj):
        # 'current_session_key' is injected by the view from request.session.
        return obj.session_key == self.context.get('current_session_key')
