from rest_framework import serializers

from id_verification.models import IdentityVerification

# Statuses that block creation of a new verification request.
ACTIVE_STATUSES = ('draft', 'submitted', 'under_review', 'action_required')


class VerificationCreateSerializer(serializers.ModelSerializer):
    """Create a new draft verification request (all document fields optional)."""

    class Meta:
        model = IdentityVerification
        fields = (
            'document_type',
            'document_number',
            'issuing_country',
            'expiry_date',
            'document_front',
            'document_back',
            'selfie',
        )
        extra_kwargs = {f: {'required': False} for f in fields}

    def validate(self, attrs):
        user = self.context['request'].user
        if IdentityVerification.objects.filter(user=user, status__in=ACTIVE_STATUSES).exists():
            raise serializers.ValidationError(
                'You already have a verification request in progress.'
            )
        return attrs


class VerificationUpdateSerializer(serializers.ModelSerializer):
    """Update a draft (or action_required) verification request."""

    class Meta:
        model = IdentityVerification
        fields = (
            'document_type',
            'document_number',
            'issuing_country',
            'expiry_date',
            'document_front',
            'document_back',
            'selfie',
        )
        extra_kwargs = {f: {'required': False} for f in fields}


class VerificationDetailSerializer(serializers.ModelSerializer):
    """Read-only representation returned to the instructor."""

    reviewed_by_email = serializers.EmailField(
        source='reviewed_by.email', read_only=True, default=None,
    )

    class Meta:
        model = IdentityVerification
        fields = (
            'id',
            'document_type',
            'document_number',
            'issuing_country',
            'expiry_date',
            'document_front',
            'document_back',
            'selfie',
            'status',
            'rejection_reason',
            'action_required_reason',
            'reviewed_by_email',
            'reviewed_at',
            'created_at',
            'submitted_at',
            'updated_at',
        )
        read_only_fields = fields


class AdminVerificationListSerializer(serializers.ModelSerializer):
    """Compact representation for admin listing."""

    instructor_name = serializers.CharField(source='user.full_name', read_only=True)
    instructor_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = IdentityVerification
        fields = (
            'id',
            'instructor_name',
            'instructor_email',
            'document_type',
            'issuing_country',
            'status',
            'submitted_at',
        )
        read_only_fields = fields


class AdminVerificationDetailSerializer(serializers.ModelSerializer):
    """Full detail view for admin review."""

    instructor_name = serializers.CharField(source='user.full_name', read_only=True)
    instructor_email = serializers.EmailField(source='user.email', read_only=True)
    reviewed_by_email = serializers.EmailField(
        source='reviewed_by.email', read_only=True, default=None,
    )

    class Meta:
        model = IdentityVerification
        fields = (
            'id',
            'instructor_name',
            'instructor_email',
            'document_type',
            'document_number',
            'issuing_country',
            'expiry_date',
            'document_front',
            'document_back',
            'selfie',
            'status',
            'rejection_reason',
            'action_required_reason',
            'admin_notes',
            'reviewed_by_email',
            'reviewed_at',
            'created_at',
            'submitted_at',
            'updated_at',
        )
        read_only_fields = fields


class AdminReviewSerializer(serializers.Serializer):
    """Input for admin review actions."""

    ACTION_CHOICES = (
        ('pick_up', 'Pick Up'),
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('request_action', 'Request Action'),
        ('expire', 'Expire'),
    )
    action = serializers.ChoiceField(choices=ACTION_CHOICES)
    rejection_reason = serializers.CharField(required=False, default='', allow_blank=True)
    action_required_reason = serializers.CharField(required=False, default='', allow_blank=True)
    admin_notes = serializers.CharField(required=False, default='', allow_blank=True)

    def validate(self, attrs):
        action = attrs['action']
        if action == 'reject' and not attrs.get('rejection_reason', '').strip():
            raise serializers.ValidationError(
                {'rejection_reason': 'A reason is required when rejecting.'}
            )
        if action == 'request_action' and not attrs.get('action_required_reason', '').strip():
            raise serializers.ValidationError(
                {'action_required_reason': 'A reason is required when requesting action.'}
            )
        return attrs
