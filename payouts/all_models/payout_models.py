import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from courses.all_models.course_models import TimestampedModel

logger = logging.getLogger(__name__)


class PayoutAccount(TimestampedModel):
    """
    Where an instructor's or partner institution's earnings get paid to.

    Exactly one of `instructor` / `institution` is set (DB check constraint),
    mirroring `payments.Order`'s exactly-one-target pattern. Must be admin
    `is_verified` before it can be targeted by payout generation — see
    docs/future_implementations/PAYOUTS.md.
    """

    class Method(models.TextChoices):
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        MOBILE_BANKING = 'mobile_banking', 'Mobile Banking'

    class MobileBankingProvider(models.TextChoices):
        BKASH = 'bkash', 'bKash'
        NAGAD = 'nagad', 'Nagad'
        ROCKET = 'rocket', 'Rocket'

    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    institution = models.ForeignKey(
        'authentication.PartnerInstitutionProfile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    payout_method = models.CharField(max_length=20, choices=Method.choices)

    # Bank transfer fields (blank unless payout_method == BANK_TRANSFER)
    bank_name = models.CharField(max_length=200, blank=True, default='')
    bank_account_number = models.CharField(max_length=50, blank=True, default='')
    bank_account_name = models.CharField(max_length=200, blank=True, default='')
    bank_routing_number = models.CharField(max_length=50, blank=True, default='')

    # Mobile banking fields (blank unless payout_method == MOBILE_BANKING)
    mobile_banking_provider = models.CharField(
        max_length=20, choices=MobileBankingProvider.choices, blank=True, default='',
    )
    mobile_banking_number = models.CharField(max_length=20, blank=True, default='')

    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'payout_accounts'
        verbose_name = 'Payout Account'
        verbose_name_plural = 'Payout Accounts'
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(instructor__isnull=False, institution__isnull=True)
                    | models.Q(instructor__isnull=True, institution__isnull=False)
                ),
                name='chk_payoutaccount_exactly_one_owner',
            ),
        ]

    @property
    def owner(self):
        """The instructor User or the institution's User — whichever is set."""
        if self.instructor_id:
            return self.instructor
        return self.institution.user if self.institution_id else None

    def __str__(self):
        who = self.instructor_id or self.institution_id
        return f'PayoutAccount({self.payout_method}, owner={who})'


class Payout(TimestampedModel):
    """
    One payout for one recipient over one admin-picked date range.

    `gross_amount` / `platform_fee_pct` / `net_amount` / `included_order_ids`
    are all snapshotted at generation time and never recomputed afterward —
    same "freeze everything" philosophy as the certificate system, so a later
    platform-fee change or a new order landing in the same period never
    silently rewrites an already-generated payout's numbers.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        PAID = 'paid', 'Paid'
        REJECTED = 'rejected', 'Rejected'

    VALID_TRANSITIONS = {
        Status.PENDING: (Status.APPROVED, Status.REJECTED),
        Status.APPROVED: (Status.PAID,),
        Status.PAID: (),
        Status.REJECTED: (),
    }

    payout_account = models.ForeignKey(
        PayoutAccount, on_delete=models.PROTECT, related_name='payouts',
    )

    period_start = models.DateField()
    period_end = models.DateField()

    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    platform_fee_pct = models.DecimalField(max_digits=5, decimal_places=2)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='BDT')

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True,
    )

    included_order_ids = models.JSONField(default=list, blank=True)

    admin_notes = models.TextField(blank=True, default='')
    rejection_reason = models.TextField(blank=True, default='')
    payment_reference = models.CharField(max_length=200, blank=True, default='')

    requested_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'payouts'
        verbose_name = 'Payout'
        verbose_name_plural = 'Payouts'
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['payout_account', 'status']),
            models.Index(fields=['status', '-requested_at']),
        ]

    def transition_to(self, new_status, rejection_reason='', payment_reference=''):
        """
        Move the payout to *new_status* with guard-rail checks.
        Raises ``ValidationError`` on illegal transitions or missing data.
        Single entry point — never set `status` directly.
        """
        from django.utils import timezone

        allowed = self.VALID_TRANSITIONS.get(self.status, ())
        if new_status not in allowed:
            raise ValidationError(
                f'Cannot transition from "{self.status}" to "{new_status}". '
                f'Allowed: {", ".join(allowed) if allowed else "none (terminal state)"}.'
            )

        if new_status == self.Status.REJECTED and not rejection_reason.strip():
            raise ValidationError(
                {'rejection_reason': 'A reason is required when rejecting a payout.'}
            )

        if new_status == self.Status.PAID and not payment_reference.strip():
            raise ValidationError(
                {'payment_reference': 'A payment reference is required when marking a payout paid.'}
            )

        now = timezone.now()
        self.status = new_status
        if new_status == self.Status.APPROVED:
            self.approved_at = now
        elif new_status == self.Status.REJECTED:
            self.rejected_at = now
            self.rejection_reason = rejection_reason
        elif new_status == self.Status.PAID:
            self.paid_at = now
            self.payment_reference = payment_reference

        self.save()
        logger.info('Payout %s transitioned to %s', self.pk, new_status)

    def __str__(self):
        return f'Payout({self.pk}, {self.status}, {self.net_amount} {self.currency})'
