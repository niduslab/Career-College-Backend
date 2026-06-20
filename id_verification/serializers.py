from django.utils import timezone
from rest_framework import serializers

from id_verification.models import IdentityVerification, InstitutionVerification

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
            'resume',
        )
        extra_kwargs = {f: {'required': False} for f in fields}

    def validate_expiry_date(self, value):
        if value and value < timezone.now().date():
            raise serializers.ValidationError('Document has already expired.')
        return value

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
            'resume',
        )
        extra_kwargs = {f: {'required': False} for f in fields}

    def validate_expiry_date(self, value):
        if value and value < timezone.now().date():
            raise serializers.ValidationError('Document has already expired.')
        return value


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
            'resume',
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
            'resume',
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


# ─────────────────────────────────────────────────────────────────────────────
# Institution verification (partner institution credential review)
# ─────────────────────────────────────────────────────────────────────────────

# Statuses that block creation of a new institution verification request.
INSTITUTION_ACTIVE_STATUSES = ('draft', 'submitted', 'under_review', 'action_required')

_INSTITUTION_DOC_FIELDS = (
    'registration_number',
    'issuing_authority',
    'official_email',
    'accreditation_document',
    'authorization_letter',
)


class InstitutionVerificationCreateSerializer(serializers.ModelSerializer):
    """Create a new draft institution verification request (all fields optional)."""

    class Meta:
        model = InstitutionVerification
        fields = _INSTITUTION_DOC_FIELDS
        extra_kwargs = {f: {'required': False} for f in fields}

    def validate(self, attrs):
        institution = self.context['institution']
        if InstitutionVerification.objects.filter(
            institution=institution, status__in=INSTITUTION_ACTIVE_STATUSES,
        ).exists():
            raise serializers.ValidationError(
                'You already have a verification request in progress.'
            )
        return attrs


class InstitutionVerificationUpdateSerializer(serializers.ModelSerializer):
    """Update a draft (or action_required) institution verification request."""

    class Meta:
        model = InstitutionVerification
        fields = _INSTITUTION_DOC_FIELDS
        extra_kwargs = {f: {'required': False} for f in fields}


class InstitutionVerificationDetailSerializer(serializers.ModelSerializer):
    """Read-only representation returned to the institution."""

    reviewed_by_email = serializers.EmailField(
        source='reviewed_by.email', read_only=True, default=None,
    )

    class Meta:
        model = InstitutionVerification
        fields = (
            'id',
            'registration_number',
            'issuing_authority',
            'official_email',
            'accreditation_document',
            'authorization_letter',
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


class AdminInstitutionVerificationListSerializer(serializers.ModelSerializer):
    """Compact representation for admin listing."""

    institution_name = serializers.CharField(
        source='institution.institution_name', read_only=True,
    )
    institution_slug = serializers.CharField(source='institution.slug', read_only=True)

    class Meta:
        model = InstitutionVerification
        fields = (
            'id',
            'institution_name',
            'institution_slug',
            'registration_number',
            'issuing_authority',
            'status',
            'submitted_at',
        )
        read_only_fields = fields


class AdminInstitutionVerificationDetailSerializer(serializers.ModelSerializer):
    """Full detail view for admin review."""

    institution_name = serializers.CharField(
        source='institution.institution_name', read_only=True,
    )
    institution_slug = serializers.CharField(source='institution.slug', read_only=True)
    reviewed_by_email = serializers.EmailField(
        source='reviewed_by.email', read_only=True, default=None,
    )

    class Meta:
        model = InstitutionVerification
        fields = (
            'id',
            'institution_name',
            'institution_slug',
            'registration_number',
            'issuing_authority',
            'official_email',
            'accreditation_document',
            'authorization_letter',
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
