"""
Institution revenue — gross earnings from paid orders on the institution's
own courses and webinars. Payments Phase 2 slice (per-institution
attribution), following the same shape as instructor_revenue_service.py.

Deliberately gross-only. No payout, balance, commission split, or bank
account — none of those have a backing model yet (CLAUDE.md's Payments
section: "Institution wallet/payout, refunds ... are Phase 2 — not built").
Every number here maps to `Order.amount` / `Order.created_at` on rows where
status='paid' and the order's course or webinar belongs to the institution.

Unlike instructor revenue (courses only), institutions also own webinars, so
paid orders are unioned across both `Order.course__partner_institution` and
`Order.webinar__partner_institution`.
"""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from analytics.services.analytics_service import _normalize_trend_params, build_value_series
from payments.all_models.order_models import Order

_DEFAULT_WINDOW_DAYS = 30
_BY_ITEM_LIMIT = 20

_SORT_WHITELIST = {
    '-paid_at': ('-paid_at',),
    'paid_at': ('paid_at',),
    '-amount': ('-amount',),
    'amount': ('amount',),
}
_DEFAULT_SORT = '-paid_at'


class InstitutionRevenueError(Exception):
    """Mirrors InstructorRevenueError — carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _paid_orders(institution):
    """Paid orders on courses or webinars the institution owns.

    Course and webinar orders are mutually exclusive per row (DB check
    constraint on Order), so this OR cannot double-count a single order.
    """
    return Order.objects.filter(
        Q(course__partner_institution=institution) | Q(webinar__partner_institution=institution),
        status=Order.Status.PAID,
    )


def _by_item(institution, limit=_BY_ITEM_LIMIT):
    course_rows = (
        _paid_orders(institution)
        .filter(course__isnull=False)
        .values('course_id', 'course__title', 'course__slug')
        .annotate(gross=Sum('amount'), paid_orders=Count('id'))
    )
    webinar_rows = (
        _paid_orders(institution)
        .filter(webinar__isnull=False)
        .values('webinar_id', 'webinar__title', 'webinar__slug')
        .annotate(gross=Sum('amount'), paid_orders=Count('id'))
    )

    rows = [
        {
            'id': r['course_id'],
            'item_type': 'course',
            'title': r['course__title'],
            'slug': r['course__slug'],
            'gross': str(r['gross'] or 0),
            'paid_orders': r['paid_orders'],
        }
        for r in course_rows
    ] + [
        {
            'id': r['webinar_id'],
            'item_type': 'webinar',
            'title': r['webinar__title'],
            'slug': r['webinar__slug'],
            'gross': str(r['gross'] or 0),
            'paid_orders': r['paid_orders'],
        }
        for r in webinar_rows
    ]
    rows.sort(key=lambda r: float(r['gross']), reverse=True)
    return rows[:limit]


def revenue_summary(institution, window_days=_DEFAULT_WINDOW_DAYS, granularity='monthly', periods=6):
    """Cards + by-item breakdown + trend. Describes the whole dataset, not a
    page — same reasoning as the instructor revenue summary."""
    now = timezone.now()
    base = _paid_orders(institution)

    gross = base.aggregate(v=Sum('amount'))['v'] or 0
    paid_orders = base.count()
    avg_order_value = round(float(gross) / paid_orders, 2) if paid_orders else 0.0

    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)
    window_gross = base.filter(created_at__gte=window_start, created_at__lt=now).aggregate(
        v=Sum('amount'))['v'] or 0
    previous_gross = base.filter(created_at__gte=prev_start, created_at__lt=window_start).aggregate(
        v=Sum('amount'))['v'] or 0
    growth_pct = (
        round((float(window_gross) - float(previous_gross)) / float(previous_gross) * 100, 1)
        if previous_gross else None
    )

    course_gross = base.filter(course__isnull=False).aggregate(v=Sum('amount'))['v'] or 0
    webinar_gross = base.filter(webinar__isnull=False).aggregate(v=Sum('amount'))['v'] or 0

    granularity, periods = _normalize_trend_params(granularity, periods)
    series = build_value_series(base, 'created_at', Sum('amount'), granularity, periods)

    return {
        'enabled': True,
        'gross': str(gross),
        'currency': 'BDT',
        'paid_orders': paid_orders,
        'window_days': window_days,
        'window_gross': str(window_gross),
        'growth_pct': growth_pct,
        'avg_order_value': avg_order_value,
        'by_item_type': {
            'course': str(course_gross),
            'webinar': str(webinar_gross),
        },
        'by_item': _by_item(institution),
        'trend': {
            'granularity': granularity,
            'periods': periods,
            'series': series,
        },
    }


def build_order_queryset(institution, params):
    """Filtered + sorted paid-order rows for the order history table."""
    qs = _paid_orders(institution)

    item_type = params.get('item_type')
    if item_type == 'course':
        qs = qs.filter(course__isnull=False)
    elif item_type == 'webinar':
        qs = qs.filter(webinar__isnull=False)
    elif item_type not in (None, '', 'all'):
        raise InstitutionRevenueError("item_type must be 'course', 'webinar', or omitted.")

    sort = (params.get('sort') or _DEFAULT_SORT).strip()
    if sort not in _SORT_WHITELIST:
        raise InstitutionRevenueError(
            f'Invalid sort. Choose one of: {", ".join(_SORT_WHITELIST)}.'
        )

    qs = qs.select_related('course', 'webinar', 'user')
    # 'id' tiebreaker keeps pagination stable across equal sort keys (many
    # orders can share the same paid_at second).
    return qs.order_by(*_SORT_WHITELIST[sort], 'id')


def serialize_order_row(order):
    if order.course_id:
        item = {'id': order.course_id, 'type': 'course', 'title': order.course.title, 'slug': order.course.slug}
    else:
        item = {'id': order.webinar_id, 'type': 'webinar', 'title': order.webinar.title, 'slug': order.webinar.slug}
    return {
        'order_id': order.id,
        'item': item,
        'learner_name': order.user.full_name,
        'amount': str(order.amount),
        'currency': order.currency,
        'paid_at': order.paid_at,
    }


def serialize_order_page(orders):
    return [serialize_order_row(o) for o in orders]
