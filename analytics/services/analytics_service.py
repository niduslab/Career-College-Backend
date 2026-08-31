"""Institution-scoped analytics aggregation.

Every query is filtered by the institution resolved from the authenticated user;
no client-supplied institution id is ever trusted. Reads are aggregate-only
(no per-row Python loops over large sets) so cost is independent of data volume.
"""

from datetime import timedelta

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone

from authentication.models import InstructorProfile
from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse
from courses.all_models.enrollment_models import Enrollment
from payments.all_models.order_models import Order
from webinars.all_models.registration_models import WebinarRegistration
from webinars.all_models.webinar_models import Webinar

# Course statuses zero-filled into every breakdown so the shape is stable.
_COURSE_STATUSES = [s.value for s in NidusCourse.CourseStatus]

# Engagement composite weights. Must sum to 1.0. Returned alongside the score.
_ENGAGEMENT_WEIGHTS = {
    'completion': 0.35,
    'active_ratio': 0.30,
    'rating': 0.20,
    'attendance': 0.15,
}

# Default rolling window (days) for growth and active-learner metrics.
_DEFAULT_WINDOW_DAYS = 30

# Trend defaults / clamps.
DEFAULT_TREND_PERIODS = 12
MAX_TREND_PERIODS = 24
DEFAULT_TOP_COURSES_LIMIT = 5
MAX_TOP_COURSES_LIMIT = 50
TOP_COURSES_SORT_OPTIONS = ('enrollments', 'rating', 'completion')


def _pct(part, whole):
    """Percentage rounded to 1 dp; 0.0 when the denominator is empty."""
    if not whole:
        return 0.0
    return round(part / whole * 100, 1)


def _course_metrics(institution):
    rows = (
        NidusCourse.objects
        .filter(partner_institution=institution)
        .values('status')
        .annotate(n=Count('id'))
    )
    breakdown = {s: 0 for s in _COURSE_STATUSES}
    total = 0
    for row in rows:
        breakdown[row['status']] = row['n']
        total += row['n']

    rating_agg = (
        NidusCourse.objects
        .filter(partner_institution=institution, is_published=True, review_count__gt=0)
        .aggregate(
            weighted=Sum(F('avg_rating') * F('review_count')),
            reviews=Sum('review_count'),
        )
    )
    total_reviews = rating_agg['reviews'] or 0
    avg_rating = round(float(rating_agg['weighted']) / total_reviews, 2) if total_reviews else 0.0

    return {
        'total': total,
        'published': breakdown[NidusCourse.CourseStatus.PUBLISHED],
        'draft': breakdown[NidusCourse.CourseStatus.DRAFT],
        'status_breakdown': breakdown,
        'avg_rating': avg_rating,
        'total_reviews': total_reviews,
    }


def _enrollment_metrics(institution, now, window_days):
    base = Enrollment.objects.filter(course__partner_institution=institution)

    active_agg = base.filter(is_active=True).aggregate(
        active=Count('id'),
        completed=Count('id', filter=Q(completed_at__isnull=False)),
        avg_progress=Avg('progress_percent'),
    )
    active = active_agg['active']
    all_time = base.count()

    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)
    current = base.filter(created_at__gte=window_start, created_at__lt=now).count()
    previous = base.filter(created_at__gte=prev_start, created_at__lt=window_start).count()
    if previous:
        growth_pct = round((current - previous) / previous * 100, 1)
    else:
        growth_pct = None  # not computable without a prior baseline

    active_learners = (
        base.filter(is_active=True, last_accessed_at__gte=window_start)
        .values('user_id')
        .distinct()
        .count()
    )

    return {
        'active': active,
        'all_time': all_time,
        'growth': {
            'current': current,
            'previous': previous,
            'growth_pct': growth_pct,
            'window_days': window_days,
        },
        'active_learners': active_learners,
        'completion_rate': _pct(active_agg['completed'], active),
        'avg_progress': round(active_agg['avg_progress'] or 0, 1),
    }


def _certificate_metrics(institution, now):
    # Revoked certificates are excluded from the "earned" counts but stay in the
    # issuance trend — the trend is a historical record of what was issued.
    base = Certificate.objects.filter(
        enrollment__course__partner_institution=institution,
        status=Certificate.Status.VALID,
    )
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        'total': base.count(),
        'this_month': base.filter(issued_at__gte=month_start).count(),
    }


def _webinar_metrics(institution, now):
    rows = (
        Webinar.objects
        .filter(partner_institution=institution)
        .values('status')
        .annotate(n=Count('id'))
    )
    status_counts = {s.value: 0 for s in Webinar.WebinarStatus}
    total = 0
    for row in rows:
        status_counts[row['status']] = row['n']
        total += row['n']

    # Time-based buckets over published, scheduled webinars. Institution webinar
    # counts are small, so classifying end-time in Python is cheap and avoids
    # DB interval-arithmetic quirks.
    upcoming = live = completed = 0
    scheduled = (
        Webinar.objects
        .filter(partner_institution=institution, is_published=True, scheduled_at__isnull=False)
        .values('scheduled_at', 'duration_minutes')
    )
    for w in scheduled:
        start = w['scheduled_at']
        end = start + timedelta(minutes=w['duration_minutes'] or 0)
        if start > now:
            upcoming += 1
        elif end >= now:
            live += 1
        else:
            completed += 1

    reg_agg = (
        WebinarRegistration.objects
        .filter(webinar__partner_institution=institution)
        .aggregate(
            active=Count('id', filter=Q(is_active=True)),
            attended=Count('id', filter=Q(attended=True)),
        )
    )

    return {
        'total': total,
        'published': status_counts[Webinar.WebinarStatus.PUBLISHED],
        'draft': status_counts[Webinar.WebinarStatus.DRAFT],
        'archived': status_counts[Webinar.WebinarStatus.ARCHIVED],
        'upcoming': upcoming,
        'live': live,
        'completed': completed,
        'registrations': reg_agg['active'],
        # attended/joined_at are reserved for an unbuilt live-day join flow, so
        # this reads 0 until that ships; the flag tells the client not to trust it.
        'attendance_rate': _pct(reg_agg['attended'], reg_agg['active']),
        'attendance_tracking_enabled': False,
    }


def _roster_metrics(institution):
    base = InstructorProfile.objects.filter(affiliated_institution=institution)
    return {
        'experts_active': base.filter(affiliation_status='active').count(),
        'experts_total': base.count(),
    }


def _revenue_metrics(institution, now, window_days):
    """Real institution gross from PAID orders on the institution's courses
    and webinars — see analytics/services/institution_revenue_service.py for
    the full revenue-page breakdown this summarizes."""
    paid = Order.objects.filter(
        Q(course__partner_institution=institution) | Q(webinar__partner_institution=institution),
        status=Order.Status.PAID,
    )
    agg = paid.aggregate(
        gross=Sum('amount'),
        course=Sum('amount', filter=Q(course__isnull=False)),
        webinar=Sum('amount', filter=Q(webinar__isnull=False)),
        orders=Count('id'),
    )

    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)
    current = paid.filter(created_at__gte=window_start, created_at__lt=now).aggregate(s=Sum('amount'))['s'] or 0
    previous = paid.filter(created_at__gte=prev_start, created_at__lt=window_start).aggregate(s=Sum('amount'))['s'] or 0
    growth_pct = round(float(current - previous) / float(previous) * 100, 1) if previous else None

    return {
        'enabled': True,
        'currency': 'BDT',
        'gross': float(agg['gross'] or 0),
        'paid_orders': agg['orders'],
        'by_item_type': {
            'course': float(agg['course'] or 0),
            'webinar': float(agg['webinar'] or 0),
        },
        'this_window': float(current),
        'growth_pct': growth_pct,
        'window_days': window_days,
    }


def _engagement_score(courses, enrollments, webinars):
    """0-100 composite health score plus its normalized components."""
    active = enrollments['active']
    # Active-learner ratio
    active_ratio = _pct(enrollments['active_learners'], active)
    components = {
        'completion': enrollments['completion_rate'],
        'active_ratio': active_ratio,
        'rating': round(courses['avg_rating'] / 5 * 100, 1),
        'attendance': webinars['attendance_rate'],
    }
    composite = round(
        sum(components[key] * weight for key, weight in _ENGAGEMENT_WEIGHTS.items()),
        1,
    )
    return {'composite': composite, 'components': components}


def institution_summary(institution, window_days=_DEFAULT_WINDOW_DAYS):
    """Full dashboard KPI payload scoped to one institution."""
    now = timezone.now()
    courses = _course_metrics(institution)
    enrollments = _enrollment_metrics(institution, now, window_days)
    webinars = _webinar_metrics(institution, now)
    return {
        'courses': courses,
        'enrollments': enrollments,
        'certificates': _certificate_metrics(institution, now),
        'webinars': webinars,
        'roster': _roster_metrics(institution),
        'revenue': _revenue_metrics(institution, now, window_days),
        'engagement_score': _engagement_score(courses, enrollments, webinars),
    }


def _period_key(dt, granularity):
    return dt.strftime('%Y-%m') if granularity == 'monthly' else dt.strftime('%Y-W%W')


def _bucket_starts(now, granularity, periods):
    """Truncated start datetimes for the last `periods` buckets, oldest first.

    Mirrors `TruncMonth`/`TruncWeek` (month → day 1; week → Monday) so the query
    filter and the series keys align exactly — no partial-bucket undercount and
    no key drift across a year boundary.
    """
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == 'weekly':
        this_start = midnight - timedelta(days=now.weekday())  # Monday of current week
        return [this_start - timedelta(weeks=periods - 1 - i) for i in range(periods)]

    month_first = midnight.replace(day=1)
    starts = []
    year, month = month_first.year, month_first.month
    for _ in range(periods):
        starts.append(month_first.replace(year=year, month=month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(starts))


def build_time_series(queryset, date_field, granularity, periods):
    """Contiguous zero-filled time series of row counts bucketed by month/week.

    SQL only returns buckets that have rows; this fills the gaps so the frontend
    gets an unbroken line.
    """
    tz = timezone.get_current_timezone()
    now = timezone.now()

    granularity = 'weekly' if granularity == 'weekly' else 'monthly'
    trunc = TruncWeek(date_field, tzinfo=tz) if granularity == 'weekly' else TruncMonth(date_field, tzinfo=tz)

    starts = _bucket_starts(now, granularity, periods)
    rows = (
        queryset
        .filter(**{f'{date_field}__gte': starts[0]})
        .annotate(period=trunc)
        .values('period')
        .annotate(count=Count('id'))
    )
    counts = {_period_key(r['period'], granularity): r['count'] for r in rows if r['period']}

    return [
        {'period': _period_key(start, granularity), 'count': counts.get(_period_key(start, granularity), 0)}
        for start in starts
    ]


def build_value_series(queryset, date_field, agg_expr, granularity, periods):
    """Contiguous zero-filled series of an aggregate VALUE (e.g. Sum) per bucket.

    Sibling of `build_time_series` for sums instead of row counts — used by the
    admin revenue trend. `agg_expr` is a Django aggregate (e.g. `Sum('amount')`).
    Empty buckets fill to `0`; every value is cast to float so the payload is
    JSON-safe (Decimal sums otherwise render as strings).
    """
    tz = timezone.get_current_timezone()
    now = timezone.now()

    granularity = 'weekly' if granularity == 'weekly' else 'monthly'
    trunc = TruncWeek(date_field, tzinfo=tz) if granularity == 'weekly' else TruncMonth(date_field, tzinfo=tz)

    starts = _bucket_starts(now, granularity, periods)
    rows = (
        queryset
        .filter(**{f'{date_field}__gte': starts[0]})
        .annotate(period=trunc)
        .values('period')
        .annotate(value=agg_expr)
    )
    values = {_period_key(r['period'], granularity): float(r['value'] or 0) for r in rows if r['period']}

    return [
        {'period': _period_key(start, granularity), 'value': values.get(_period_key(start, granularity), 0.0)}
        for start in starts
    ]


def _normalize_trend_params(granularity, periods):
    granularity = 'weekly' if granularity == 'weekly' else 'monthly'
    try:
        periods = int(periods)
    except (TypeError, ValueError):
        periods = DEFAULT_TREND_PERIODS
    periods = max(1, min(periods, MAX_TREND_PERIODS))
    return granularity, periods


def enrollment_trend(institution, granularity='monthly', periods=DEFAULT_TREND_PERIODS):
    granularity, periods = _normalize_trend_params(granularity, periods)
    qs = Enrollment.objects.filter(course__partner_institution=institution)
    return granularity, periods, build_time_series(qs, 'created_at', granularity, periods)


def webinar_registration_trend(institution, granularity='monthly', periods=DEFAULT_TREND_PERIODS):
    granularity, periods = _normalize_trend_params(granularity, periods)
    qs = WebinarRegistration.objects.filter(webinar__partner_institution=institution)
    return granularity, periods, build_time_series(qs, 'created_at', granularity, periods)


def certificate_trend(institution, granularity='monthly', periods=DEFAULT_TREND_PERIODS):
    granularity, periods = _normalize_trend_params(granularity, periods)
    qs = Certificate.objects.filter(enrollment__course__partner_institution=institution)
    return granularity, periods, build_time_series(qs, 'issued_at', granularity, periods)


def top_courses(institution, sort='enrollments', limit=DEFAULT_TOP_COURSES_LIMIT):
    """Ranked published courses for the institution by enrollments / rating / completion."""
    if sort not in TOP_COURSES_SORT_OPTIONS:
        sort = 'enrollments'
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_TOP_COURSES_LIMIT
    limit = max(1, min(limit, MAX_TOP_COURSES_LIMIT))

    qs = (
        NidusCourse.objects
        .filter(partner_institution=institution)
        .annotate(
            enrollment_count=Count('enrollments', filter=Q(enrollments__is_active=True)),
            completed_count=Count(
                'enrollments',
                filter=Q(enrollments__is_active=True, enrollments__completed_at__isnull=False),
            ),
        )
    )

    if sort == 'rating':
        qs = qs.order_by('-avg_rating', '-review_count')
    elif sort == 'completion':
        qs = qs.order_by('-completed_count', '-enrollment_count')
    else:
        qs = qs.order_by('-enrollment_count')

    results = []
    for course in qs[:limit]:
        results.append({
            'id': course.id,
            'title': course.title,
            'slug': course.slug,
            'status': course.status,
            'enrollments': course.enrollment_count,
            'completion_rate': _pct(course.completed_count, course.enrollment_count),
            'avg_rating': float(course.avg_rating),
            'review_count': course.review_count,
        })
    return results
