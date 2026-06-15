from rest_framework import serializers

from .models import Notification, NotificationCategory, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id', 'event_type', 'title', 'body', 'data',
            'is_read', 'read_at', 'created_at',
        ]


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ['category', 'email_enabled', 'push_enabled']


class MarkReadSerializer(serializers.Serializer):
    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )
    all = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        if not data.get('all'):
            ids = data.get('ids')
            if ids is None:
                raise serializers.ValidationError('Provide ids or set all=true.')
            if len(ids) == 0:
                raise serializers.ValidationError('ids must not be empty.')
        return data


class PreferencePatchSerializer(serializers.Serializer):
    """Accepts {category_name: {email_enabled: bool, push_enabled: bool}} patches."""

    def to_internal_value(self, data):
        valid_categories = {c.value for c in NotificationCategory}
        result = {}
        for key, value in data.items():
            if key not in valid_categories:
                raise serializers.ValidationError({key: 'Unknown category.'})
            if not isinstance(value, dict):
                raise serializers.ValidationError({key: 'Expected an object.'})
            inner = {}
            if 'email_enabled' in value:
                if not isinstance(value['email_enabled'], bool):
                    raise serializers.ValidationError({key: {'email_enabled': 'Must be boolean.'}})
                inner['email_enabled'] = value['email_enabled']
            if 'push_enabled' in value:
                if not isinstance(value['push_enabled'], bool):
                    raise serializers.ValidationError({key: {'push_enabled': 'Must be boolean.'}})
                inner['push_enabled'] = value['push_enabled']
            result[key] = inner
        return result
