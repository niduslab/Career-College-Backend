"""
Payout generation + admin review workflow.

Phase 1 is manual/admin-driven: `generate_payouts` computes gross revenue per
verified PayoutAccount over an admin-picked date range (reusing the same
paid-order filters the revenue analytics services already use), snapshots the
platform commission rate, and creates `pending` Payout rows. Admins then
approve/reject and finally mark-paid after transferring money outside the
system — see docs/future_implementations/PAYOUTS.md.

Every mutation writes an AdminActionLog row in the same transaction so the
audit trail can never drift from the action.
"""
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from admin_console.all_models import AdminActionLog
from admin_console.all_models.platform_settings_models import PlatformSettings
from admin_console.services.user_admin_service import log_admin_action
from payments.all_models.order_models import Order
from payouts.all_models.payout_models import Payout, PayoutAccount

logger = logging.getLogger(__name__)


class PayoutError(Exception):
    """Raised on payout business-rule violations. Carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


_ACCOUNT_SORT_WHITELIST = {
    '-created_at': ('-created_at', '-id'),
    'created_at': ('created_at', 'id'),
}

_PAYOUT_SORT_WHITELIST = {
    '-requested_at': ('-requested_at', '-id'),
    'requested_at': ('requested_at', 'id'),
    '-net_amount': ('-net_amount', '-id'),
    'net_amount': ('net_amount', 'id'),
}


def _paid_orders_for_account(payout_account, period_start=None, period_end=None):
    """
    Paid orders attributable to a payout account's owner, optionally
    restricted to a date range (by `Order.created_at`).

    Mirrors the exact ownership filters already used by
    analytics/services/instructor_revenue_service.py and
    institution_revenue_service.py — reused here, not reinvented, so a
    payout's gross amount always agrees with what the recipient sees on
    their own revenue dashboard.
    """
    if payout_account.instructor_id:
        qs = Order.objects.filter(
            Q(course__instructors=payout_account.instructor)
            | Q(course__created_by=payout_account.instructor),
            status=Order.Status.PAID,
        ).distinct()
    elif payout_account.institution_id:
        qs = Order.objects.filter(
            Q(course__partner_institution=payout_account.institution)
            | Q(webinar__partner_institution=payout_account.institution),
            status=Order.Status.PAID,
        )
    else:
        return Order.objects.none()

    if period_start is not None:
        qs = qs.filter(created_at__date__gte=period_start)
    if period_end is not None:
        qs = qs.filter(created_at__date__lte=period_end)
    return qs


def search_payout_accounts(params):
    """Admin browse of payout accounts. Read-only. ?is_verified= filter, ?sort= whitelist."""
    qs = PayoutAccount.objects.select_related('instructor', 'institution')

    is_verified = params.get('is_verified')
    if is_verified is not None:
        parsed = str(is_verified).strip().lower()
        if parsed in ('true', '1', 'yes'):
            qs = qs.filter(is_verified=True)
        elif parsed in ('false', '0', 'no'):
            qs = qs.filter(is_verified=False)

    sort = params.get('sort') or '-created_at'
    return qs.order_by(*_ACCOUNT_SORT_WHITELIST.get(sort, ('-created_at', '-id')))


def search_payouts(params):
    """Admin browse of payouts. Read-only. ?status= / ?search= (recipient name) filters."""
    qs = Payout.objects.select_related(
        'payout_account', 'payout_account__instructor', 'payout_account__institution',
    )

    status = (params.get('status') or '').strip()
    if status:
        if status not in Payout.Status.values:
            raise PayoutError('Invalid status filter.', 400)
        qs = qs.filter(status=status)

    search = (params.get('search') or '').strip()
    if len(search) >= 2:
        qs = qs.filter(
            Q(payout_account__instructor__full_name__icontains=search)
            | Q(payout_account__instructor__email__icontains=search)
            | Q(payout_account__institution__institution_name__icontains=search)
        )

    sort = params.get('sort') or '-requested_at'
    return qs.order_by(*_PAYOUT_SORT_WHITELIST.get(sort, ('-requested_at', '-id')))


@transaction.atomic
def verify_payout_account(actor, pk):
    """Admin confirms a payout account's bank/mobile-banking details are correct."""
    from django.utils import timezone

    try:
        account = PayoutAccount.objects.select_for_update().get(pk=pk)
    except PayoutAccount.DoesNotExist:
        raise PayoutError('Payout account not found.', 404)

    if account.is_verified:
        raise PayoutError('This payout account is already verified.', 422)

    account.is_verified = True
    account.verified_at = timezone.now()
    account.save(update_fields=['is_verified', 'verified_at', 'updated_at'])

    log_admin_action(
        actor, AdminActionLog.Action.PAYOUT_ACCOUNT_VERIFY, account.owner,
        metadata={'payout_account_id': account.pk},
    )
    logger.info('admin %s verified payout account %s', actor.pk, account.pk)
    return account


@transaction.atomic
def generate_payouts(actor, period_start, period_end):
    """
    Create `pending` Payout rows for every verified PayoutAccount with
    gross_amount > 0 in [period_start, period_end]. Skips accounts that
    already have a Payout covering the exact same period (idempotent re-run).
    """
    if period_start > period_end:
        raise PayoutError('period_start must be on or before period_end.', 400)

    commission_pct = PlatformSettings.load().default_commission_pct
    created = []

    accounts = PayoutAccount.objects.filter(is_verified=True).select_related(
        'instructor', 'institution',
    )
    for account in accounts:
        already_exists = Payout.objects.filter(
            payout_account=account, period_start=period_start, period_end=period_end,
        ).exists()
        if already_exists:
            continue

        orders = _paid_orders_for_account(account, period_start, period_end)
        gross = orders.aggregate(v=Sum('amount'))['v'] or 0
        if gross <= 0:
            continue

        order_ids = list(orders.values_list('id', flat=True))
        net = gross * (1 - commission_pct / 100)

        payout = Payout.objects.create(
            payout_account=account,
            period_start=period_start,
            period_end=period_end,
            gross_amount=gross,
            platform_fee_pct=commission_pct,
            net_amount=net,
            included_order_ids=order_ids,
        )
        created.append(payout)

    logger.info(
        'admin %s generated %d payouts for period %s to %s',
        actor.pk, len(created), period_start, period_end,
    )
    return created


@transaction.atomic
def review_payout(actor, pk, action, rejection_reason=''):
    """Approve or reject a pending payout."""
    try:
        payout = Payout.objects.select_for_update().select_related(
            'payout_account', 'payout_account__instructor', 'payout_account__institution',
        ).get(pk=pk)
    except Payout.DoesNotExist:
        raise PayoutError('Payout not found.', 404)

    if action not in ('approve', 'reject'):
        raise PayoutError('action must be "approve" or "reject".', 400)

    new_status = Payout.Status.APPROVED if action == 'approve' else Payout.Status.REJECTED

    try:
        payout.transition_to(new_status, rejection_reason=rejection_reason)
    except ValidationError as e:
        if hasattr(e, 'message_dict'):
            raise PayoutError(next(iter(e.message_dict.values()))[0], 400)
        raise PayoutError(e.messages[0], 422)

    log_admin_action(
        actor,
        AdminActionLog.Action.PAYOUT_APPROVE if action == 'approve' else AdminActionLog.Action.PAYOUT_REJECT,
        payout.payout_account.owner,
        reason=rejection_reason,
        metadata={'payout_id': payout.pk, 'net_amount': str(payout.net_amount)},
    )
    logger.info('admin %s %sd payout %s', actor.pk, action, payout.pk)
    return payout


@transaction.atomic
def mark_payout_paid(actor, pk, payment_reference):
    """Terminal step: admin confirms the money was actually transferred (off-system)."""
    try:
        payout = Payout.objects.select_for_update().select_related(
            'payout_account', 'payout_account__instructor', 'payout_account__institution',
        ).get(pk=pk)
    except Payout.DoesNotExist:
        raise PayoutError('Payout not found.', 404)

    try:
        payout.transition_to(Payout.Status.PAID, payment_reference=payment_reference)
    except ValidationError as e:
        if hasattr(e, 'message_dict'):
            raise PayoutError(next(iter(e.message_dict.values()))[0], 400)
        raise PayoutError(e.messages[0], 422)

    log_admin_action(
        actor, AdminActionLog.Action.PAYOUT_MARK_PAID, payout.payout_account.owner,
        metadata={'payout_id': payout.pk, 'payment_reference': payment_reference},
    )
    logger.info('admin %s marked payout %s paid', actor.pk, payout.pk)
    return payout
