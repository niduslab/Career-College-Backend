"""Celery tasks for the payments app."""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from payments.all_models.order_models import Order
from payments.services import PaymentError, mark_order_failed, reconcile_pending_order

logger = logging.getLogger(__name__)


@shared_task
def reap_stale_processing_orders_task(stale_minutes: int = 15, abandon_hours: int = 24):
    """Reconcile orders stuck in `initiated`/`processing`.

    A learner can pay and then never trigger a callback (tab closed after
    payment, IPN endpoint unreachable, browser died mid-redirect) — leaving the
    order in `processing` with money taken and no access granted, and nothing
    else recovers it. This queries the gateway by `tran_id`:

    - gateway says VALID   → finalize (mark paid + grant access)
    - gateway says FAILED  → mark failed
    - still pending, and older than `abandon_hours` → mark failed (abandoned)
    - gateway unreachable   → leave it; next run retries

    Scheduled via CELERY_BEAT_SCHEDULE in settings.py.
    """
    now = timezone.now()
    stale_cutoff = now - timedelta(minutes=stale_minutes)
    abandon_cutoff = now - timedelta(hours=abandon_hours)

    stale = Order.objects.filter(
        status__in=[Order.Status.INITIATED, Order.Status.PROCESSING],
        updated_at__lt=stale_cutoff,
    )

    tally = {'paid': 0, 'failed': 0, 'pending': 0, 'abandoned': 0, 'errors': 0}
    for order in stale.iterator():
        try:
            outcome = reconcile_pending_order(order)
            if outcome == 'pending' and order.updated_at < abandon_cutoff:
                mark_order_failed(order.tran_id, payload={'reason': 'abandoned_by_reaper'})
                outcome = 'abandoned'
            tally[outcome] += 1
        except PaymentError:
            # Gateway unreachable — retry on the next scheduled run.
            tally['errors'] += 1
        except Exception:
            logger.exception('Order reconciliation failed: tran_id=%s', order.tran_id)
            tally['errors'] += 1

    if tally['paid'] or tally['failed'] or tally['abandoned'] or tally['errors']:
        logger.warning('Reaped stale orders: %s', tally)
    return tally
