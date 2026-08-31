"""Platform-wide (admin) analytics aggregation.

The admin counterpart to `analytics_service` (institution-scoped). Same
read-only, aggregate-only philosophy — the only difference is scope: **no
institution filter**, so every number spans the whole platform. Gated to
admins in the view layer.

Unlike the partner dashboard, revenue IS computed here: `payments.Order`
records real money, and at admin scope there is no payout/attribution
complexity (admins see every order), so `revenue.enabled=True`.
"""

from datetime import timedelta

from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone

from authentication.models import User
from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse
from courses.all_models.enrollment_models import Enrollment
from payments.all_models.order_models import Order
from webinars.all_models.registration_models import WebinarRegistration
from webinars.all_models.webinar_models import Webinar

# Reuse the partner service's building blocks — no duplication.
from analytics.services.analytics_service import (
    DEFAULT_TOP_COURSES_LIMIT,
    DEFAULT_TREND_PERIODS,
    MAX_TOP_COURSES_LIMIT,
    TOP_COURSES_SORT_OPTIONS,
    _COURSE_STATUSES,
    _normalize_trend_params,
    _pct,
    build_time_series,
    build_value_series,
)

_DEFAULT_WINDOW_DAYS = 30


# --------------------------------------------------------------------------- #
# Summary sub-metrics
# --------------------------------------------------------------------------- #
def _user_metrics(now, window_days):
    rows = User.objects.values('user_type').annotate(n=Count('id'))
    by_type = {choice[0]: 0 for choice in User.USER_TYPE_CHOICES}
    total = 0
    for row in rows:
        by_type[row['user_type']] = row['n']
        total += row['n']

    agg = User.objects.aggregate(
        active=Count('id', filter=Q(is_active=True)),
        verified=Count('id', filter=Q(is_email_verified=True)),
    )

    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)
    current = User.objects.filter(registration_date__gte=window_start, registration_date__lt=now).count()
    previous = User.objects.filter(registration_date__gte=prev_start, registration_date__lt=window_start).count()
    growth_pct = round((current - previous) / previous * 100, 1) if previous else None

    return {
        'total': total,
        'active': agg['active'],
        'email_verified': agg['verified'],
        'by_type': by_type,
        'new_this_window': current,
        'growth_pct': growth_pct,
    }


def _course_metrics():
    rows = NidusCourse.objects.values('status').annotate(n=Count('id'))
    breakdown = {s: 0 for s in _COURSE_STATUSES}
    total = 0
    for row in rows:
        breakdown[row['status']] = row['n']
        total += row['n']

    rating_agg = (
        NidusCourse.objects
        .filter(is_published=True, review_count__gt=0)
        .aggregate(weighted=Sum(F('avg_rating') * F('review_count')), reviews=Sum('review_count'))
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


def _enrollment_metrics(now, window_days):
    base = Enrollment.objects.all()
    active_agg = base.filter(is_active=True).aggregate(
        active=Count('id'),
        completed=Count('id', filter=Q(completed_at__isnull=False)),
        avg_progress=Avg('progress_percent'),
    )
    active = active_agg['active']

    type_agg = base.aggregate(
        free=Count('id', filter=Q(enrollment_type=Enrollment.EnrollmentType.FREE)),
        paid=Count('id', filter=Q(enrollment_type=Enrollment.EnrollmentType.PAID)),
    )

    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)
    current = base.filter(created_at__gte=window_start, created_at__lt=now).count()
    previous = base.filter(created_at__gte=prev_start, created_at__lt=window_start).count()
    growth_pct = round((current - previous) / previous * 100, 1) if previous else None

    return {
        'total': base.count(),
        'active': active,
        'completed': active_agg['completed'],
        'completion_rate': _pct(active_agg['completed'], active),
        'avg_progress': round(active_agg['avg_progress'] or 0, 1),
        'by_type': {'free': type_agg['free'], 'paid': type_agg['paid']},
        'new_this_window': current,
        'growth_pct': growth_pct,
    }


def _certificate_metrics(now):
    # Revoked certificates are excluded from the "earned" counts but stay in the
    # issuance trend — the trend is a historical record of what was issued.
    base = Certificate.objects.filter(status=Certificate.Status.VALID)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        'total': base.count(),
        'this_month': base.filter(issued_at__gte=month_start).count(),
    }


def _webinar_metrics(now):
    rows = Webinar.objects.values('status').annotate(n=Count('id'))
    status_counts = {s.value: 0 for s in Webinar.WebinarStatus}
    total = 0
    for row in rows:
        status_counts[row['status']] = row['n']
        total += row['n']

    # Small published set → classify end-time in Python (same as the partner service).
    upcoming = live = completed = 0
    scheduled = (
        Webinar.objects
        .filter(is_published=True, scheduled_at__isnull=False)
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

    registrations = WebinarRegistration.objects.filter(is_active=True).count()

    return {
        'total': total,
        'published': status_counts[Webinar.WebinarStatus.PUBLISHED],
        'draft': status_counts[Webinar.WebinarStatus.DRAFT],
        'archived': status_counts[Webinar.WebinarStatus.ARCHIVED],
        'upcoming': upcoming,
        'live': live,
        'completed': completed,
        'registrations': registrations,
    }


def _revenue_metrics(now, window_days):
    """Real platform gross from PAID orders — enabled (payments records money)."""
    paid = Order.objects.filter(status=Order.Status.PAID)
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


def platform_summary(window_days=_DEFAULT_WINDOW_DAYS):
    """Full platform KPI payload (admin scope, no institution filter)."""
    now = timezone.now()
    return {
        'users': _user_metrics(now, window_days),
        'courses': _course_metrics(),
        'enrollments': _enrollment_metrics(now, window_days),
        'certificates': _certificate_metrics(now),
        'webinars': _webinar_metrics(now),
        'revenue': _revenue_metrics(now, window_days),
    }


# --------------------------------------------------------------------------- #
# Trends
# --------------------------------------------------------------------------- #
def user_signup_trend(granularity='monthly', periods=DEFAULT_TREND_PERIODS):
    granularity, periods = _normalize_trend_params(granularity, periods)
    return granularity, periods, build_time_series(User.objects.all(), 'registration_date', granularity, periods)


def enrollment_trend(granularity='monthly', periods=DEFAULT_TREND_PERIODS):
    granularity, periods = _normalize_trend_params(granularity, periods)
    return granularity, periods, build_time_series(Enrollment.objects.all(), 'created_at', granularity, periods)


def certificate_trend(granularity='monthly', periods=DEFAULT_TREND_PERIODS):
    granularity, periods = _normalize_trend_params(granularity, periods)
    return granularity, periods, build_time_series(Certificate.objects.all(), 'issued_at', granularity, periods)


def revenue_trend(granularity='monthly', periods=DEFAULT_TREND_PERIODS):
    granularity, periods = _normalize_trend_params(granularity, periods)
    qs = Order.objects.filter(status=Order.Status.PAID)
    return granularity, periods, build_value_series(qs, 'created_at', Sum('amount'), granularity, periods)


# --------------------------------------------------------------------------- #
# Top courses (platform-wide)
# --------------------------------------------------------------------------- #
def top_courses(sort='enrollments', limit=DEFAULT_TOP_COURSES_LIMIT):
    """Ranked courses across the whole platform by enrollments / rating / completion."""
    if sort not in TOP_COURSES_SORT_OPTIONS:
        sort = 'enrollments'
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_TOP_COURSES_LIMIT
    limit = max(1, min(limit, MAX_TOP_COURSES_LIMIT))

    qs = NidusCourse.objects.annotate(
        enrollment_count=Count('enrollments', filter=Q(enrollments__is_active=True)),
        completed_count=Count(
            'enrollments',
            filter=Q(enrollments__is_active=True, enrollments__completed_at__isnull=False),
        ),
    )

    if sort == 'rating':
        qs = qs.order_by('-avg_rating', '-review_count')
    elif sort == 'completion':
        qs = qs.order_by('-completed_count', '-enrollment_count')
    else:
        qs = qs.order_by('-enrollment_count')

    return [
        {
            'id': course.id,
            'title': course.title,
            'slug': course.slug,
            'status': course.status,
            'enrollments': course.enrollment_count,
            'completion_rate': _pct(course.completed_count, course.enrollment_count),
            'avg_rating': float(course.avg_rating),
            'review_count': course.review_count,
        }
        for course in qs[:limit]
    ]


# --------------------------------------------------------------------------- #
# Conversion funnel
# --------------------------------------------------------------------------- #
def conversion_funnel():
    """Distinct-learner funnel: signup → enrolled → completed → certified.

    Every stage is scoped to the same population — current (non-deleted) learner
    accounts — so the funnel stays monotonically non-increasing. FK joins bypass
    the User manager's `is_deleted` filter, so it is applied explicitly; without
    it a soft-deleted or role-changed enroller would count in a later stage but
    not in `signup`, pushing `from_prev_pct` above 100%.
    """
    signup = User.objects.filter(user_type='learner').count()
    enrolled = (
        Enrollment.objects
        .filter(user__is_deleted=False, user__user_type='learner')
        .values('user_id').distinct().count()
    )
    completed = (
        Enrollment.objects
        .filter(user__is_deleted=False, user__user_type='learner', completed_at__isnull=False)
        .values('user_id').distinct().count()
    )
    certified = (
        Certificate.objects
        .filter(
            enrollment__user__is_deleted=False,
            enrollment__user__user_type='learner',
            status=Certificate.Status.VALID,
        )
        .values('enrollment__user_id').distinct().count()
    )

    stages = [
        {'key': 'signup', 'label': 'Signed up', 'count': signup},
        {'key': 'enrolled', 'label': 'Enrolled', 'count': enrolled, 'from_prev_pct': _pct(enrolled, signup)},
        {'key': 'completed', 'label': 'Completed', 'count': completed, 'from_prev_pct': _pct(completed, enrolled)},
        {'key': 'certified', 'label': 'Certified', 'count': certified, 'from_prev_pct': _pct(certified, completed)},
    ]
    return {'stages': stages}
