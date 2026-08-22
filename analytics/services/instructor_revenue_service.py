"""
Instructor revenue — gross earnings from paid orders on the instructor's own
courses. See docs/architecture/31-instructor-revenue.md.

Deliberately gross-only. No payout, balance, commission split, or bank
account — none of those have a backing model (CLAUDE.md's Payments section:
"Institution wallet/payout, refunds ... are Phase 2 — not built"). Every
number here maps to `Order.amount` / `Order.created_at` / `Order.course` on
rows where status='paid'.
"""

from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from analytics.services.analytics_service import _normalize_trend_params, build_value_series
from analytics.services.instructor_students_service import instructor_course_options
from courses.all_models.course_models import NidusCourse
from payments.all_models.order_models import Order

_DEFAULT_WINDOW_DAYS = 30
_BY_COURSE_LIMIT = 20

_SORT_WHITELIST = {
    '-paid_at': ('-paid_at',),
    'paid_at': ('paid_at',),
    '-amount': ('-amount',),
    'amount': ('amount',),
}
_DEFAULT_SORT = '-paid_at'


class InstructorRevenueError(Exception):
    """Mirrors InstructorStudentsError — carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _paid_orders(instructor):
    """Paid orders on courses the instructor owns, by either ownership path.

    `.distinct()` dedupes `Order` rows — the OR fans out on `NidusCourse`
    (instructors / created_by), not on `Order`, so each order still has
    exactly one course and this cannot double-count amounts.
    """
    return Order.objects.filter(
        Q(course__instructors=instructor) | Q(course__created_by=instructor),
        status=Order.Status.PAID,
    ).distinct()


def _resolve_owned_course(instructor, course_id):
    try:
        course_id = int(course_id)
    except (TypeError, ValueError):
        raise InstructorRevenueError('course_id must be an integer.')

    owns = (
        NidusCourse.objects
        .filter(Q(instructors=instructor) | Q(created_by=instructor), pk=course_id)
        .exists()
    )
    if not owns:
        raise InstructorRevenueError('Course not found.', http_status=404)
    return course_id


def _by_course(instructor, limit=_BY_COURSE_LIMIT):
    rows = (
        _paid_orders(instructor)
        .values('course_id', 'course__title', 'course__slug')
        .annotate(gross=Sum('amount'), paid_orders=Count('id'))
        .order_by('-gross')[:limit]
    )
    return [
        {
            'id': r['course_id'],
            'title': r['course__title'],
            'slug': r['course__slug'],
            'gross': str(r['gross'] or 0),
            'paid_orders': r['paid_orders'],
        }
        for r in rows
    ]


def revenue_summary(instructor, window_days=_DEFAULT_WINDOW_DAYS, granularity='monthly', periods=6):
    """Cards + by-course breakdown + trend. Describes the whole dataset, not
    a page — same reasoning as the students roster summary."""
    now = timezone.now()
    base = _paid_orders(instructor)

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

    granularity, periods = _normalize_trend_params(granularity, periods)
    series = build_value_series(base, 'created_at', Sum('amount'), granularity, periods)

    return {
        'gross': str(gross),
        'currency': 'BDT',
        'paid_orders': paid_orders,
        'window_days': window_days,
        'window_gross': str(window_gross),
        'growth_pct': growth_pct,
        'avg_order_value': avg_order_value,
        'by_course': _by_course(instructor),
        'trend': {
            'granularity': granularity,
            'periods': periods,
            'series': series,
        },
        'courses': instructor_course_options(instructor),
    }


def build_order_queryset(instructor, params):
    """Filtered + sorted paid-order rows for the order history table."""
    qs = _paid_orders(instructor)

    course_id = params.get('course_id')
    if course_id not in (None, '', 'all'):
        qs = qs.filter(course_id=_resolve_owned_course(instructor, course_id))

    sort = (params.get('sort') or _DEFAULT_SORT).strip()
    if sort not in _SORT_WHITELIST:
        raise InstructorRevenueError(
            f'Invalid sort. Choose one of: {", ".join(_SORT_WHITELIST)}.'
        )

    qs = qs.select_related('course', 'user')
    # 'id' tiebreaker keeps pagination stable across equal sort keys (many
    # orders can share the same paid_at second).
    return qs.order_by(*_SORT_WHITELIST[sort], 'id')


def serialize_order_row(order):
    return {
        'order_id': order.id,
        'course': {
            'id': order.course_id,
            'title': order.course.title,
            'slug': order.course.slug,
        },
        'learner_name': order.user.full_name,
        'amount': str(order.amount),
        'currency': order.currency,
        'paid_at': order.paid_at,
    }


def serialize_order_page(orders):
    return [serialize_order_row(o) for o in orders]
