import re

from rest_framework import serializers


def validate_custom_password_strength(value):
    """Shared custom password-strength checks across serializers."""
    if len(value) < 8:
        raise serializers.ValidationError("Password must be at least 8 characters long.")

    if not re.search(r'[a-zA-Z]', value):
        raise serializers.ValidationError("Password must contain at least one letter.")

    if not re.search(r'\d', value):
        raise serializers.ValidationError("Password must contain at least one number.")

    weak_passwords = ['password', '12345678', 'qwerty', 'abc123', 'password123']
    if value.lower() in weak_passwords:
        raise serializers.ValidationError("This password is too common. Choose a stronger password.")

    return value
