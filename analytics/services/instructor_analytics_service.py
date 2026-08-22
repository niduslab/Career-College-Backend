"""
Instructor-scoped analytics aggregation.

Mirrors the query patterns in `analytics_service.py` (conditional
aggregation, weighted average, growth-window comparison) but resolves scope
from the requesting user directly — an instructor has no institution profile
to key off. See docs/architecture/29-instructor-dashboard-analytics.md.

Every number here is derived from a table that already exists — no invented
metrics. Watch-time trend and traffic-source breakdown are deliberately
absent (see the doc for why); do not add them without a real backing model
first.
"""

from datetime import timedelta

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from courses.all_models.course_models import NidusCourse
from courses.all_models.enrollment_models import Enrollment
from payments.all_models.order_models import Order

_COURSE_STATUSES = [s.value for s in NidusCourse.CourseStatus]
_DEFAULT_WINDOW_DAYS = 30


def _pct(part, whole):
    if not whole:
        return 0.0
    return round(part / whole * 100, 1)


def _instructor_course_filter(instructor):
    """An instructor's own courses come from two relations — co-instructor
    roster membership and individual authorship. `.distinct()` is required:
    a course where the instructor is both would otherwise double-count."""
    return Q(instructors=instructor) | Q(created_by=instructor)


def _revenue_metrics(instructor, now, window_days):
    # Order has no direct instructor field — go through course.
    base = Order.objects.filter(
        Q(course__instructors=instructor) | Q(course__created_by=instructor),
        status=Order.Status.PAID,
    ).distinct()

    gross = base.aggregate(total=Sum('amount'))['total'] or 0

    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)
    current = base.filter(paid_at__gte=window_start, paid_at__lt=now).aggregate(
        total=Sum('amount'))['total'] or 0
    previous = base.filter(paid_at__gte=prev_start, paid_at__lt=window_start).aggregate(
        total=Sum('amount'))['total'] or 0
    growth_pct = round((current - previous) / previous * 100, 1) if previous else None

    return {
        'gross': str(gross),
        'currency': 'BDT',
        'paid_orders': base.count(),
        'growth_pct': growth_pct,
    }


def _course_metrics(instructor):
    rows = (
        NidusCourse.objects
        .filter(_instructor_course_filter(instructor))
        .distinct()
        .values('status')
        .annotate(n=Count('id'))
    )
    breakdown = {s: 0 for s in _COURSE_STATUSES}
    total = 0
    for row in rows:
        breakdown[row['status']] = row['n']
        total += row['n']

    return {
        'total': total,
        'published': breakdown[NidusCourse.CourseStatus.PUBLISHED],
        'draft': breakdown[NidusCourse.CourseStatus.DRAFT],
        'by_status': breakdown,
    }


def _rating_metrics(instructor):
    agg = (
        NidusCourse.objects
        .filter(_instructor_course_filter(instructor), review_count__gt=0)
        .distinct()
        .aggregate(
            weighted=Sum(F('avg_rating') * F('review_count')),
            reviews=Sum('review_count'),
        )
    )
    total_reviews = agg['reviews'] or 0
    avg_rating = round(float(agg['weighted']) / total_reviews, 2) if total_reviews else 0.0
    return {'avg_rating': avg_rating, 'review_count': total_reviews}


def _student_metrics(instructor, now, window_days):
    base = Enrollment.objects.filter(
        Q(course__instructors=instructor) | Q(course__created_by=instructor),
    ).distinct()

    total = base.values('user_id').distinct().count()
    active = base.filter(is_active=True).values('user_id').distinct().count()

    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)
    current = base.filter(created_at__gte=window_start, created_at__lt=now).count()
    previous = base.filter(created_at__gte=prev_start, created_at__lt=window_start).count()
    growth_pct = round((current - previous) / previous * 100, 1) if previous else None

    return {'total': total, 'active': active, 'growth_pct': growth_pct}


def _funnel_metrics(instructor):
    """enrolled -> started -> completed, distinct learners per stage. Reuses
    the already-persisted `progress_percent`/`completed_at` fields — no
    reimplementation of recalculate_progress() needed."""
    base = Enrollment.objects.filter(
        Q(course__instructors=instructor) | Q(course__created_by=instructor),
    ).distinct()

    enrolled = base.values('user_id').distinct().count()
    started = base.filter(progress_percent__gt=0).values('user_id').distinct().count()
    completed = base.filter(completed_at__isnull=False).values('user_id').distinct().count()

    return {'enrolled': enrolled, 'started': started, 'completed': completed}


def _top_courses(instructor, limit=5):
    qs = (
        NidusCourse.objects
        .filter(_instructor_course_filter(instructor))
        .distinct()
        .annotate(
            enrollment_count=Count('enrollments', filter=Q(enrollments__is_active=True), distinct=True),
        )
        .order_by('-enrollment_count')
    )

    # Revenue per course computed separately — Order has no direct FK usable
    # in the same annotate without duplicating the enrollment join's row count.
    course_ids = list(qs.values_list('id', flat=True)[:limit])
    revenue_by_course = {
        row['course_id']: row['total']
        for row in (
            Order.objects
            .filter(course_id__in=course_ids, status=Order.Status.PAID)
            .values('course_id')
            .annotate(total=Sum('amount'))
        )
    }

    results = []
    for course in qs[:limit]:
        results.append({
            'id': course.id,
            'title': course.title,
            'slug': course.slug,
            'enrollments': course.enrollment_count,
            'avg_rating': float(course.avg_rating),
            'revenue': str(revenue_by_course.get(course.id, 0)),
        })
    return results


def instructor_summary(instructor, window_days=_DEFAULT_WINDOW_DAYS):
    """Full dashboard KPI payload scoped to one instructor's own courses."""
    now = timezone.now()
    return {
        'revenue': _revenue_metrics(instructor, now, window_days),
        'students': _student_metrics(instructor, now, window_days),
        'courses': _course_metrics(instructor),
        'rating': _rating_metrics(instructor),
        'funnel': _funnel_metrics(instructor),
        'top_courses': _top_courses(instructor),
    }
