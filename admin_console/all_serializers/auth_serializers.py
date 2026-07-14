from django.contrib.auth import authenticate
from rest_framework import serializers

from authentication.models import User


class AdminLoginSerializer(serializers.Serializer):
    """
    Validate admin login credentials for the session-based admin console.

    Mirrors ``UserLoginSerializer`` account-state checks (soft-deleted,
    inactive/restricted, unverified email) so behaviour and messages match the
    JWT login path. The admin-role gate (``is_staff``/``user_type == 'admin'``)
    is enforced in the view, not here, so it can return 403 rather than 400.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        return value.lower().strip()

    def validate(self, attrs):
        email = attrs['email']
        password = attrs['password']
        generic_error = 'Invalid email or password.'

        # Look up including soft-deleted for consistent timing (no enumeration).
        user = User.objects.all_with_deleted().filter(email__iexact=email).first()

        if not user or user.is_deleted:
            raise serializers.ValidationError(generic_error)

        if not user.is_active or user.is_restricted_by_admin:
            raise serializers.ValidationError(
                'Your account has been deactivated or restricted. Please contact support.'
            )

        if not user.is_email_verified:
            raise serializers.ValidationError(
                'Please verify your email before logging in.'
            )

        user = authenticate(email=email, password=password)
        if user is None:
            raise serializers.ValidationError(generic_error)

        attrs['user'] = user
        return attrs
