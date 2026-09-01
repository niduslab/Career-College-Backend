from rest_framework import serializers

from courses.all_serializers.course_serializers import (
    InstructorBriefSerializer,
    PartnerInstitutionBriefSerializer,
)
from payouts.all_models.payout_models import Payout, PayoutAccount


class PayoutAccountSerializer(serializers.ModelSerializer):
    """Read serializer — used by both the self-service `me/` endpoint and the admin list."""

    instructor = InstructorBriefSerializer(read_only=True)
    institution = PartnerInstitutionBriefSerializer(read_only=True)

    class Meta:
        model = PayoutAccount
        fields = [
            'id',
            'instructor',
            'institution',
            'payout_method',
            'bank_name',
            'bank_account_number',
            'bank_account_name',
            'bank_routing_number',
            'mobile_banking_provider',
            'mobile_banking_number',
            'is_verified',
            'verified_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'instructor', 'institution', 'is_verified', 'verified_at', 'created_at', 'updated_at']


class PayoutAccountWriteSerializer(serializers.ModelSerializer):
    """Self-service create/update — `instructor`/`institution` are set from `request.user` in the view, never client-supplied."""

    class Meta:
        model = PayoutAccount
        fields = [
            'payout_method',
            'bank_name',
            'bank_account_number',
            'bank_account_name',
            'bank_routing_number',
            'mobile_banking_provider',
            'mobile_banking_number',
        ]

    def validate(self, attrs):
        method = attrs.get('payout_method') or getattr(self.instance, 'payout_method', None)
        if method == PayoutAccount.Method.BANK_TRANSFER:
            for field in ('bank_name', 'bank_account_number', 'bank_account_name'):
                value = attrs.get(field) or getattr(self.instance, field, '')
                if not (value or '').strip():
                    raise serializers.ValidationError({field: 'Required for bank transfer.'})
        elif method == PayoutAccount.Method.MOBILE_BANKING:
            for field in ('mobile_banking_provider', 'mobile_banking_number'):
                value = attrs.get(field) or getattr(self.instance, field, '')
                if not (value or '').strip():
                    raise serializers.ValidationError({field: 'Required for mobile banking.'})
        return attrs

    def save(self, **kwargs):
        """Re-verification: any edit to a verified account resets it to unverified."""
        instance = super().save(**kwargs)
        if instance.is_verified:
            instance.is_verified = False
            instance.verified_at = None
            instance.save(update_fields=['is_verified', 'verified_at', 'updated_at'])
        return instance


class PayoutSerializer(serializers.ModelSerializer):
    payout_account = PayoutAccountSerializer(read_only=True)

    class Meta:
        model = Payout
        fields = [
            'id',
            'payout_account',
            'period_start',
            'period_end',
            'gross_amount',
            'platform_fee_pct',
            'net_amount',
            'currency',
            'status',
            'included_order_ids',
            'admin_notes',
            'rejection_reason',
            'payment_reference',
            'requested_at',
            'approved_at',
            'paid_at',
            'rejected_at',
        ]
        read_only_fields = fields
