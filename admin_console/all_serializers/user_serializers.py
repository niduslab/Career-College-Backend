from rest_framework import serializers

from admin_console.all_models import AdminActionLog
from authentication.models import User


class AdminUserListSerializer(serializers.ModelSerializer):
    """User row for the admin list — identity + every account-state flag.

    ``institution_name``/``institution_type`` are populated only for
    ``user_type='partner_institution'`` accounts (null otherwise) — the admin
    console has no other surface that shows which institution an account is.
    """

    institution_name = serializers.SerializerMethodField()
    institution_type = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'full_name',
            'name_slug',
            'user_type',
            'is_email_verified',
            'is_verified',
            'is_active',
            'is_restricted_by_admin',
            'is_deleted',
            'is_staff',
            'registration_date',
            'institution_name',
            'institution_type',
        ]
        read_only_fields = fields

    def get_institution_name(self, obj):
        profile = getattr(obj, 'partner_institution_profile', None)
        return profile.institution_name if profile else None

    def get_institution_type(self, obj):
        profile = getattr(obj, 'partner_institution_profile', None)
        return profile.institution_type if profile else None


class AdminUserDetailSerializer(AdminUserListSerializer):
    """Full detail — adds soft-delete + timestamp fields."""

    class Meta(AdminUserListSerializer.Meta):
        fields = AdminUserListSerializer.Meta.fields + [
            'deleted_at',
            'deletion_reason',
            'updated_at',
        ]
        read_only_fields = fields


class _UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'full_name', 'email']
        read_only_fields = fields


class AdminActionLogSerializer(serializers.ModelSerializer):
    """Read-only view of one audit row."""

    actor = _UserBriefSerializer(read_only=True)
    target_user = _UserBriefSerializer(read_only=True)

    class Meta:
        model = AdminActionLog
        fields = ['id', 'action', 'actor', 'target_user', 'reason', 'metadata', 'created_at']
        read_only_fields = fields
