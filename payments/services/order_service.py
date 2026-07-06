"""Order lifecycle: checkout creation and payment finalization.

State machine: initiated → processing → paid | failed | cancelled.
PAID is terminal — fail/cancel callbacks can never clobber it.

`finalize_payment` is the single trusted path to PAID. Both the IPN and the
success redirect funnel into it; it re-validates with the SSLCommerz
Validation API and is idempotent under concurrent double-fire.
"""

import logging
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from courses.all_models.enrollment_models import Enrollment
from courses.services.enrollment_service import enroll_learner
from payments.all_models.order_models import Order
from payments.services.exceptions import PaymentError
from payments.services.sslcommerz_service import (
    initiate_session,
    query_transaction,
    validate_transaction,
)

logger = logging.getLogger(__name__)

_VALID_STATUSES = ('VALID', 'VALIDATED')


def _new_tran_id():
    # SSLCommerz caps tran_id at 30 chars; "CC" + 24 hex = 26.
    return f"CC{uuid4().hex[:24].upper()}"


class _ValidationRejected(Exception):
    """Internal: aborts the finalize transaction so the FAILED write can be
    persisted outside it (a save inside a raising atomic block rolls back)."""

    def __init__(self, order, data, reason):
        super().__init__(reason)
        self.order = order
        self.data = data
        self.reason = reason


def _guard_course_checkout(user, course):
    if not course.is_published:
        raise PaymentError('Course not found.', http_status=404)
    if course.price <= 0:
        raise PaymentError('This course is free. Use the enroll endpoint instead.', http_status=422)
    if Enrollment.objects.filter(user=user, course=course, is_active=True).exists():
        raise PaymentError('You are already enrolled in this course.', http_status=422)
    if Order.objects.filter(user=user, course=course, status=Order.Status.PAID).exists():
        raise PaymentError(
            'You already purchased this course. Use the enroll endpoint to regain access.',
            http_status=422,
        )


def _guard_webinar_checkout(user, webinar):
    from webinars.models import WebinarRegistration

    if not webinar.is_published:
        raise PaymentError('Webinar not found.', http_status=404)
    if webinar.price <= 0:
        raise PaymentError('This webinar is free. Use the register endpoint instead.', http_status=422)
    if webinar.scheduled_at and webinar.scheduled_at <= timezone.now():
        raise PaymentError('This webinar has already started.', http_status=422)
    if WebinarRegistration.objects.filter(user=user, webinar=webinar, is_active=True).exists():
        raise PaymentError('You are already registered for this webinar.', http_status=422)
    if webinar.max_capacity is not None:
        active = WebinarRegistration.objects.filter(webinar=webinar, is_active=True).count()
        if active >= webinar.max_capacity:
            raise PaymentError('This webinar has reached its capacity.', http_status=422)
    if Order.objects.filter(user=user, webinar=webinar, status=Order.Status.PAID).exists():
        raise PaymentError(
            'You already purchased this webinar. Use the register endpoint to regain access.',
            http_status=422,
        )


def create_checkout(user, course=None, webinar=None):
    """Create an Order for a paid course OR webinar and open a gateway session.
    Returns (order, gateway_url). Exactly one target must be given.

    Raises PaymentError on any guard violation or gateway failure.
    """
    if (course is None) == (webinar is None):
        raise PaymentError('Provide exactly one of course or webinar.', http_status=400)

    if course is not None:
        _guard_course_checkout(user, course)
        target_filter = {'course': course}
        amount = course.price
    else:
        _guard_webinar_checkout(user, webinar)
        target_filter = {'webinar': webinar}
        amount = webinar.price

    # DB work first; the gateway network call stays OUTSIDE the transaction so
    # a slow/hung gateway can't hold row locks open.
    with transaction.atomic():
        # Supersede stale pending sessions — only the newest checkout is live.
        stale = (
            Order.objects
            .select_for_update()
            .filter(
                user=user, **target_filter,
                status__in=[Order.Status.INITIATED, Order.Status.PROCESSING],
            )
        )
        stale.update(status=Order.Status.CANCELLED)
        order = Order.objects.create(
            user=user,
            **target_filter,
            amount=amount,
            currency='BDT',
            tran_id=_new_tran_id(),
            status=Order.Status.INITIATED,
        )

    try:
        session = initiate_session(order, user)
    except PaymentError:
        order.status = Order.Status.FAILED
        order.save(update_fields=['status', 'updated_at'])
        raise

    order.status = Order.Status.PROCESSING
    order.gateway_payload = {'session': session}
    order.save(update_fields=['status', 'gateway_payload', 'updated_at'])
    return order, session['GatewayPageURL']


def _fail_order(order, validation_data, reason):
    order.status = Order.Status.FAILED
    order.gateway_payload = {**order.gateway_payload, 'validation': validation_data}
    order.save(update_fields=['status', 'gateway_payload', 'updated_at'])
    logger.warning('Payment validation rejected (%s): tran_id=%s', reason, order.tran_id)


def _verification_failure(order, data):
    """Return a reason string when the validation payload doesn't match OUR
    order, or None when every check passes. Pure — no writes."""
    if data.get('status') not in _VALID_STATUSES:
        return f"status={data.get('status')}"
    if data.get('tran_id') != order.tran_id:
        return 'tran_id mismatch'
    try:
        paid_amount = Decimal(str(data.get('amount', '0')))
    except InvalidOperation:
        paid_amount = Decimal('-1')
    if paid_amount != order.amount:
        return f'amount mismatch: {paid_amount} != {order.amount}'
    if data.get('currency') != order.currency:
        return f"currency mismatch: {data.get('currency')}"
    response_store_id = data.get('store_id')
    if response_store_id is None:
        # Some gateway products (e.g. sandbox EasyCheckout) omit store_id. Tolerate
        # that in sandbox only — in production a missing store_id fails closed so a
        # response can't dodge the store-ownership check by simply not carrying it.
        if not settings.SSLCOMMERZ_SANDBOX:
            return 'store_id missing from validation response'
        logger.warning(
            'Validation response has no store_id (sandbox); skipping check: tran_id=%s', order.tran_id,
        )
    elif response_store_id != settings.SSLCOMMERZ_STORE_ID:
        return f'store_id mismatch: {response_store_id}'
    return None


def finalize_payment(tran_id, val_id):
    """Validate with SSLCommerz and, on success, mark the Order PAID and create
    the PAID enrollment atomically. Idempotent — safe under double IPN and
    redirect+IPN races. Returns the Order.

    Raises PaymentError(404) for an unknown tran_id, PaymentError(422) when
    validation fails, PaymentError(503) when the gateway is unreachable.
    """
    order = Order.objects.filter(tran_id=tran_id).select_related('course', 'webinar', 'user').first()
    if order is None:
        raise PaymentError('Order not found.', http_status=404)

    # Cheap short-circuit before the network call.
    if order.status == Order.Status.PAID:
        return order

    data = validate_transaction(val_id)

    try:
        with transaction.atomic():
            # of=('self',) — course/webinar are nullable FKs (LEFT JOIN); Postgres
            # can't FOR UPDATE the nullable side, and we only need the order row locked.
            order = (
                Order.objects
                .select_for_update(of=('self',))
                .select_related('course', 'webinar', 'user')
                .get(pk=order.pk)
            )
            if order.status == Order.Status.PAID:  # lost the race — already finalized
                return order

            # ── Verify everything against OUR order; the gateway response is
            # untrusted input until each field matches. The FAILED write happens
            # OUTSIDE this block — raising inside would roll it back. ──
            failure_reason = _verification_failure(order, data)
            if failure_reason is not None:
                raise _ValidationRejected(order, data, failure_reason)

            # ── Duplicate payment: another session for the same (user, target)
            # already completed. Detected two ways: (1) a sibling PAID row is
            # visible now, or (2) the race where two sessions both pass this
            # check and the partial-unique constraint rejects the second PAID
            # save (caught as IntegrityError below). Both route to the same
            # record-and-flag-for-refund handling. ──
            target_filter = (
                {'course': order.course} if order.course_id else {'webinar': order.webinar}
            )
            already_paid = (
                Order.objects
                .filter(user=order.user, status=Order.Status.PAID, **target_filter)
                .exclude(pk=order.pk)
                .exists()
            )
            if already_paid:
                return _record_duplicate_payment(order, data, val_id)

            order.status = Order.Status.PAID
            order.val_id = val_id
            order.paid_at = timezone.now()
            order.gateway_payload = {**order.gateway_payload, 'validation': data}
            order.save(update_fields=['status', 'val_id', 'paid_at', 'gateway_payload', 'updated_at'])

            _grant_access(order)

            transaction.on_commit(lambda: _dispatch_payment_notification(order, successful=True))
    except _ValidationRejected as rejected:
        # The transaction above rolled back; persist the FAILED state now.
        _fail_order(rejected.order, rejected.data, rejected.reason)
        raise PaymentError('Payment validation failed.', http_status=422)
    except IntegrityError:
        # Lost the race: a sibling PAID row landed between our check and save,
        # tripping the partial-unique constraint. The transaction above rolled
        # back — re-open, lock, and record the duplicate for manual refund.
        with transaction.atomic():
            dup = (
                Order.objects
                .select_for_update(of=('self',))
                .select_related('course', 'webinar', 'user')
                .get(pk=order.pk)
            )
            return _record_duplicate_payment(dup, data, val_id)

    return order


def _record_duplicate_payment(order, data, val_id):
    """The learner paid twice for the same target. A second PAID row is
    impossible (partial-unique), so mark this one FAILED + requires_refund,
    still grant access (they did pay), and page for a manual refund. Caller
    must hold a row lock on `order`. Returns the order."""
    order.status = Order.Status.FAILED
    order.val_id = val_id
    order.gateway_payload = {
        **order.gateway_payload, 'validation': data, 'requires_refund': True,
    }
    order.save(update_fields=['status', 'val_id', 'gateway_payload', 'updated_at'])
    logger.critical(
        'Duplicate payment requires manual refund: tran_id=%s user=%s %s=%s',
        order.tran_id, order.user_id, order.item_type, order.item.pk,
    )
    _grant_access(order)
    return order


def _grant_access(order):
    """Grant what the order paid for: a PAID enrollment (course) or an active
    registration (webinar). Money already moved — tolerate already-has-access
    and (for webinars) publish/capacity states that changed mid-payment."""
    if order.course_id:
        try:
            enroll_learner(
                order.user,
                order.course,
                enrollment_type=Enrollment.EnrollmentType.PAID,
                allow_unpublished=True,  # money already moved; honor it regardless of publish state
            )
        except ValidationError:
            # Already actively enrolled (e.g. admin granted access mid-payment) — nothing to do.
            pass
        return

    from webinars.services.registration_service import register_for_webinar
    from webinars.services.webinar_service import WebinarError

    try:
        register_for_webinar(order.user, order.webinar, via_payment=True)
    except WebinarError:
        # Already actively registered — money is recorded honestly either way.
        pass


def _terminal_mark(tran_id, new_status, payload):
    """Shared fail/cancel transition. No-op when the order is PAID (terminal)
    or unknown. Returns the Order or None."""
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(tran_id=tran_id).first()
        if order is None:
            logger.warning('Callback for unknown order ignored: tran_id=%s', tran_id)
            return None
        if order.status == Order.Status.PAID:
            # PAID is terminal — a late fail/cancel callback must never clobber it.
            return order
        if order.status == new_status:
            return order
        order.status = new_status
        if payload:
            order.gateway_payload = {**order.gateway_payload, 'callback': payload}
            order.save(update_fields=['status', 'gateway_payload', 'updated_at'])
        else:
            order.save(update_fields=['status', 'updated_at'])
        if new_status == Order.Status.FAILED:
            transaction.on_commit(lambda: _dispatch_payment_notification(order, successful=False))
        return order


def mark_order_failed(tran_id, payload=None):
    """Mark an order failed (gateway fail/expired callback). PAID is never clobbered."""
    return _terminal_mark(tran_id, Order.Status.FAILED, payload)


def mark_order_cancelled(tran_id, payload=None):
    """Mark an order cancelled (user backed out at the gateway)."""
    return _terminal_mark(tran_id, Order.Status.CANCELLED, payload)


def reconcile_pending_order(order):
    """Resolve a stuck `initiated`/`processing` order by asking the gateway what
    actually happened to its `tran_id`. Used by the reaper for orders where
    neither the success redirect nor the IPN ever landed (tab closed after
    paying, IPN unreachable, etc.). Returns 'paid' | 'failed' | 'pending'.

    Raises PaymentError(503) if the gateway is unreachable — the caller (task)
    swallows that so the order is retried on the next run.
    """
    data = query_transaction(order.tran_id)
    elements = data.get('element') or []
    match = next((e for e in elements if e.get('tran_id') == order.tran_id), None)

    if match and match.get('status') in _VALID_STATUSES and match.get('val_id'):
        finalize_payment(order.tran_id, match['val_id'])
        return 'paid'
    if match and match.get('status') in ('FAILED', 'EXPIRED', 'CANCELLED', 'UNATTEMPTED'):
        mark_order_failed(order.tran_id, payload={'reconcile': data})
        return 'failed'
    # No record, or still open at the gateway — leave it; the reaper's hard-age
    # cutoff abandons it if this persists.
    return 'pending'


def get_learner_orders(user, status_filter=None):
    """The caller's own orders, newest first. Raises PaymentError(400) on a bad status."""
    qs = Order.objects.filter(user=user).select_related('course', 'webinar')
    if status_filter:
        if status_filter not in Order.Status.values:
            raise PaymentError(
                f"Invalid status. Valid values: {', '.join(Order.Status.values)}.",
                http_status=400,
            )
        qs = qs.filter(status=status_filter)
    return qs


def _dispatch_payment_notification(order, *, successful):
    from notifications.models import NotificationEventType
    from notifications.services.dispatcher import dispatch

    event = (
        NotificationEventType.PAYMENT_SUCCESSFUL if successful
        else NotificationEventType.PAYMENT_FAILED
    )
    item = order.item
    dispatch(
        event,
        [order.user],
        context={
            'item_type': order.item_type,
            'item_title': item.title,
            'item_slug': item.slug,
            'amount': str(order.amount),
            'currency': order.currency,
            'tran_id': order.tran_id,
        },
    )
