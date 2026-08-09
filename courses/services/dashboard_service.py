"""Read-only aggregates powering the learner dashboard.

Every number here is derived from tables that already exist — no new counters,
no denormalized cache. Where a metric cannot be computed honestly from that
data it is either omitted (total XP) or explicitly flagged approximate
(day streak); see the notes on each function.
"""

import heapq
import itertools
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Avg, Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from courses.all_models.assessment_models import (
    AssignmentSubmission,
    CodingSubmission,
    QuizAttempt,
)
from courses.all_models.certificate_models import Certificate
from courses.all_models.content_models import WatchProgress
from courses.all_models.course_models import CourseSection
from courses.all_models.enrollment_models import Enrollment
from courses.services.activity_service import get_activity_dates
from courses.services.enrollment_service import get_learner_enrollments
from courses.services.learner_service import load_learner_curriculum

# Streak lookback. Bounds the streak scan to a few months of one learner's
# activity rows, so cost is O(1) in dataset size and deep history can't slow
# the tile. At one row per active day, that is at most 120 rows.
STREAK_WINDOW_DAYS = 120

# Hard cap on the merged activity feed. Makes deep pagination impossible to
# abuse — a dashboard widget is not an archive.
ACTIVITY_WINDOW = 200

ACTIVITY_TYPES = (
    'lecture_completed',
    'quiz_submitted',
    'assignment_submitted',
    'coding_submitted',
    'course_enrolled',
    'certificate_earned',
)

UPCOMING_DEFAULT_DAYS = 30
UPCOMING_MAX_DAYS = 365
UPCOMING_DEFAULT_LIMIT = 20
UPCOMING_MAX_LIMIT = 50


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _enrollment_metrics(user) -> dict:
    """One conditional aggregate over the learner's enrollments."""
    row = Enrollment.objects.filter(user=user).aggregate(
        enrolled=Count('id', filter=Q(is_active=True)),
        in_progress=Count('id', filter=Q(is_active=True, completed_at__isnull=True)),
        completed=Count('id', filter=Q(completed_at__isnull=False)),
        avg_progress=Avg('progress_percent', filter=Q(is_active=True)),
    )
    return {
        'courses_enrolled': row['enrolled'] or 0,
        'courses_in_progress': row['in_progress'] or 0,
        'courses_completed': row['completed'] or 0,
        'average_progress_percent': round(float(row['avg_progress'] or 0), 1),
    }


def _watch_metrics(user) -> dict:
    row = WatchProgress.objects.filter(user=user).aggregate(
        seconds=Sum('watched_seconds'),
        lectures=Count('id', filter=Q(is_completed=True)),
    )
    seconds = row['seconds'] or 0
    return {
        'total_learning_seconds': seconds,
        'total_learning_hours': round(seconds / 3600, 1),
        'lectures_completed': row['lectures'] or 0,
    }


def _compute_day_streak(user, now) -> int:
    """Consecutive days of activity ending today (or yesterday — grace day).

    Reads `LearnerActivityDay`, an append-only record written by every
    learner-side content read and submission. It replaced a union over four
    consumption tables, which could not be made accurate: three of them were
    fine (`auto_now_add` submission timestamps), but `WatchProgress
    .last_watched_at` is `auto_now`, so it stored only the latest touch per
    lecture — re-reading a finished article recorded nothing at all, and
    re-opening an old lecture erased the historical date it carried.

    The grace rule prevents the streak reading 0 at 00:01 before the learner
    has had a chance to study.
    """
    since = timezone.localdate(now) - timedelta(days=STREAK_WINDOW_DAYS)
    dates = get_activity_dates(user, since)
    if not dates:
        return 0

    today = timezone.localdate(now)
    cursor = today if today in dates else today - timedelta(days=1)
    if cursor not in dates:
        return 0

    streak = 0
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def get_learner_summary(user) -> dict:
    """KPI tiles for the learner dashboard. Four constant queries.

    Two caveats are part of the contract:

    `total_learning_seconds` sums `WatchProgress.watched_seconds`, which
    `upsert_watch_progress` stores as the furthest playback cursor position
    clamped to the video duration — not accumulated watch time. Re-watching a
    lecture does not increase it. Rows for courses the learner later
    unenrolled from are included, because `unenroll_learner` is an explicit
    soft-revoke that preserves progress.

    `day_streak` is exact going forward — it reads `LearnerActivityDay`, an
    append-only record. `day_streak_is_approximate` is therefore False, and
    is kept in the response rather than dropped so it can flip back if a
    per-user timezone ever lands (days are currently bucketed in the
    platform-wide `settings.TIME_ZONE`, reported as `day_streak_timezone`).
    Activity from before that table existed was backfilled best-effort from
    the old consumption tables; see migration 0030.

    `total_xp` is deliberately absent. It is not derivable from any existing
    table, and any invented formula would be retroactively unstable (changing
    the weights silently rewrites every learner's history) and could not back
    an XP timeline or leaderboard. `LearnerActivityDay` does not fill that
    gap: XP needs one row per scoring event with a points value, which is the
    opposite de-duplication rule. It needs its own ledger.
    """
    now = timezone.now()

    data = _enrollment_metrics(user)
    data.update(_watch_metrics(user))
    data['certificates_earned'] = Certificate.objects.filter(enrollment__user=user).count()
    data['day_streak'] = _compute_day_streak(user, now)
    data['day_streak_is_approximate'] = False
    data['day_streak_timezone'] = timezone.get_current_timezone_name()
    return data


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------

def _course_ref(course) -> dict:
    return {
        'id': course.id,
        'title': course.title,
        'slug': course.slug,
        'thumbnail': course.thumbnail.url if course.thumbnail else None,
    }


def _watch_items(user, limit):
    rows = (
        WatchProgress.objects
        .filter(user=user, is_completed=True)
        .select_related('lecture__section__course')
        .order_by('-last_watched_at')[:limit]
    )
    return [
        {
            'id': f'watch:{row.pk}',
            'type': 'lecture_completed',
            'occurred_at': row.last_watched_at,
            'title': row.lecture.title,
            'course': _course_ref(row.lecture.section.course),
            'meta': {'lecture_id': row.lecture_id},
        }
        for row in rows
    ]


def _quiz_items(user, limit):
    rows = (
        QuizAttempt.objects
        .filter(user=user)
        .select_related('quiz__section__course')
        .order_by('-submitted_at')[:limit]
    )
    return [
        {
            'id': f'quiz:{row.pk}',
            'type': 'quiz_submitted',
            'occurred_at': row.submitted_at,
            'title': row.quiz.title,
            'course': _course_ref(row.quiz.section.course),
            'meta': {
                'quiz_id': row.quiz_id,
                'attempt_id': row.pk,
                'score': row.score,
                'max_score': row.max_score,
            },
        }
        for row in rows
    ]


def _assignment_items(user, limit):
    rows = (
        AssignmentSubmission.objects
        .filter(user=user)
        .select_related('assignment__section__course')
        .order_by('-submitted_at')[:limit]
    )
    return [
        {
            'id': f'assignment:{row.pk}',
            'type': 'assignment_submitted',
            'occurred_at': row.submitted_at,
            'title': row.assignment.title,
            'course': _course_ref(row.assignment.section.course),
            'meta': {
                'assignment_id': row.assignment_id,
                'submission_id': row.pk,
                'status': row.status,
                'total_score': row.total_score,
                'max_score': row.max_score,
            },
        }
        for row in rows
    ]


def _coding_items(user, limit):
    rows = (
        CodingSubmission.objects
        .filter(user=user)
        .select_related('exercise__section__course')
        .order_by('-submitted_at')[:limit]
    )
    return [
        {
            'id': f'coding:{row.pk}',
            'type': 'coding_submitted',
            'occurred_at': row.submitted_at,
            'title': row.exercise.title,
            'course': _course_ref(row.exercise.section.course),
            'meta': {
                'exercise_id': row.exercise_id,
                'submission_id': row.pk,
                'status': row.status,
                'passed_tests': row.passed_tests,
                'total_tests': row.total_tests,
            },
        }
        for row in rows
    ]


def _enrollment_items(user, limit):
    rows = (
        Enrollment.objects
        .filter(user=user)
        .select_related('course')
        .order_by('-created_at')[:limit]
    )
    return [
        {
            'id': f'enrollment:{row.pk}',
            'type': 'course_enrolled',
            'occurred_at': row.created_at,
            'title': row.course.title,
            'course': _course_ref(row.course),
            'meta': {'enrollment_id': row.pk, 'enrollment_type': row.enrollment_type},
        }
        for row in rows
    ]


def _certificate_items(user, limit):
    rows = (
        Certificate.objects
        .filter(enrollment__user=user)
        .select_related('enrollment__course')
        .order_by('-issued_at')[:limit]
    )
    return [
        {
            'id': f'certificate:{row.pk}',
            'type': 'certificate_earned',
            'occurred_at': row.issued_at,
            'title': row.course_title,
            'course': _course_ref(row.enrollment.course),
            'meta': {
                'certificate_uid': str(row.certificate_uid),
                'download_url': reverse(
                    'courses:certificate-download',
                    kwargs={'certificate_uid': str(row.certificate_uid)},
                ),
            },
        }
        for row in rows
    ]


_ACTIVITY_BUILDERS = {
    'lecture_completed': _watch_items,
    'quiz_submitted': _quiz_items,
    'assignment_submitted': _assignment_items,
    'coding_submitted': _coding_items,
    'course_enrolled': _enrollment_items,
    'certificate_earned': _certificate_items,
}


def get_learner_activity_feed(user, params) -> list[dict]:
    """Merged recent-activity feed, newest first, capped at ACTIVITY_WINDOW.

    One indexed, `select_related` query per source — six by default, fewer when
    `?type=` narrows the set. Each source is individually sorted DESC, so a
    k-way merge of their capped heads yields exactly the true top-K of the
    union. Cost does not grow with dataset size or page depth.

    Raises ValidationError on an unrecognised `?type=` value.
    """
    requested = []
    raw_types = params.getlist('type') if hasattr(params, 'getlist') else [params.get('type')]
    for raw in raw_types:
        if not raw:
            continue
        requested.extend(part.strip() for part in raw.split(',') if part.strip())

    if requested:
        unknown = [t for t in requested if t not in _ACTIVITY_BUILDERS]
        if unknown:
            raise ValidationError({
                'type': (
                    f'Invalid type(s): {", ".join(unknown)}. '
                    f'Must be one of: {", ".join(ACTIVITY_TYPES)}.'
                )
            })
        selected = [t for t in ACTIVITY_TYPES if t in requested]
    else:
        selected = list(ACTIVITY_TYPES)

    sources = [_ACTIVITY_BUILDERS[name](user, ACTIVITY_WINDOW) for name in selected]
    merged = heapq.merge(*sources, key=lambda item: item['occurred_at'], reverse=True)
    return list(itertools.islice(merged, ACTIVITY_WINDOW))


# ---------------------------------------------------------------------------
# Upcoming
# ---------------------------------------------------------------------------

def _clamp(raw, default, maximum, field):
    if raw in (None, ''):
        return default
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({field: f'"{raw}" is not a valid integer.'})
    if parsed < 1:
        raise ValidationError({field: 'Must be at least 1.'})
    return min(parsed, maximum)


def _schedule_items(user, now, horizon, limit):
    """Cohort start and end dates for the learner's active cohort enrollments."""
    starts = (
        Enrollment.objects
        .filter(
            user=user,
            is_active=True,
            schedule__isnull=False,
            schedule__start_date__gt=now,
            schedule__start_date__lte=horizon,
        )
        .select_related('schedule', 'course')
        .order_by('schedule__start_date')[:limit]
    )
    ends = (
        Enrollment.objects
        .filter(
            user=user,
            is_active=True,
            schedule__isnull=False,
            schedule__start_date__lte=now,
            schedule__end_date__gt=now,
            schedule__end_date__lte=horizon,
        )
        .select_related('schedule', 'course')
        .order_by('schedule__end_date')[:limit]
    )

    items = [
        {
            'type': 'course_starts',
            'occurs_at': row.schedule.start_date,
            'title': row.course.title,
            'subtitle': row.schedule.cohort_label,
            'course': _course_ref(row.course),
            'webinar': None,
            'meta': {'schedule_id': row.schedule_id, 'enrollment_id': row.pk},
        }
        for row in starts
    ]
    items += [
        {
            'type': 'course_ends',
            'occurs_at': row.schedule.end_date,
            'title': row.course.title,
            'subtitle': row.schedule.cohort_label,
            'course': _course_ref(row.course),
            'webinar': None,
            'meta': {'schedule_id': row.schedule_id, 'enrollment_id': row.pk},
        }
        for row in ends
    ]
    return items


def _section_unlock_items(user, now, horizon, limit):
    """Drip-release dates on sections of courses the learner is enrolled in.

    `.distinct()` is required: a learner may hold both a self-paced and a
    cohort enrollment for the same course, which duplicates every section
    through the join.
    """
    rows = (
        CourseSection.objects
        .filter(
            course__enrollments__user=user,
            course__enrollments__is_active=True,
            unlocks_at__gt=now,
            unlocks_at__lte=horizon,
        )
        .select_related('course')
        .distinct()
        .order_by('unlocks_at')[:limit]
    )
    return [
        {
            'type': 'section_unlocks',
            'occurs_at': row.unlocks_at,
            'title': row.title,
            'subtitle': row.course.title,
            'course': _course_ref(row.course),
            'webinar': None,
            'meta': {'section_id': row.pk},
        }
        for row in rows
    ]


def _webinar_items(user, now, horizon, limit):
    from webinars.all_models.registration_models import WebinarRegistration

    rows = (
        WebinarRegistration.objects
        .filter(
            user=user,
            is_active=True,
            webinar__scheduled_at__gt=now,
            webinar__scheduled_at__lte=horizon,
        )
        .select_related('webinar')
        .order_by('webinar__scheduled_at')[:limit]
    )
    return [
        {
            'type': 'webinar_starts',
            'occurs_at': row.webinar.scheduled_at,
            'title': row.webinar.title,
            'subtitle': None,
            'course': None,
            'webinar': {
                'id': row.webinar_id,
                'title': row.webinar.title,
                'slug': row.webinar.slug,
            },
            'meta': {'registration_id': row.pk},
        }
        for row in rows
    ]


def get_learner_upcoming(user, params) -> dict:
    """Upcoming cohort, drip-release and webinar dates, soonest first.

    Not paginated: an enrolled learner has a handful of cohorts and
    registrations, and paginating an ascending union across four sources would
    need a cursor per source for no benefit. Bounded by `?days=` and `?limit=`
    instead. Four indexed queries.
    """
    days = _clamp(params.get('days'), UPCOMING_DEFAULT_DAYS, UPCOMING_MAX_DAYS, 'days')
    limit = _clamp(params.get('limit'), UPCOMING_DEFAULT_LIMIT, UPCOMING_MAX_LIMIT, 'limit')

    now = timezone.now()
    horizon = now + timedelta(days=days)

    items = (
        _schedule_items(user, now, horizon, limit)
        + _section_unlock_items(user, now, horizon, limit)
        + _webinar_items(user, now, horizon, limit)
    )
    items.sort(key=lambda item: item['occurs_at'])
    items = items[:limit]

    return {'horizon_days': days, 'count': len(items), 'items': items}


# ---------------------------------------------------------------------------
# Continue learning
# ---------------------------------------------------------------------------

def _first_incomplete_lecture(curriculum):
    """Walk an ordered curriculum payload for the next lecture to watch.

    Returns (item_or_None, section_or_None, earliest_locked_unlocks_at). Locked
    sections are skipped but their unlock time is recorded, so the caller can
    tell "nothing left" apart from "everything left is still locked".
    """
    locked_until = None
    for section in curriculum['sections']:
        if section['is_locked']:
            unlocks_at = section.get('unlocks_at')
            if unlocks_at is not None and (locked_until is None or unlocks_at < locked_until):
                locked_until = unlocks_at
            continue
        for item in section['items']:
            if item['item_type'] == 'lecture' and not item.get('is_completed'):
                return item, section, locked_until
    return None, None, locked_until


def get_continue_target(user):
    """Resume target: most-recently-accessed active enrollment + next lecture.

    Built entirely on existing services. `get_learner_enrollments` already
    orders by `last_accessed_at DESC NULLS LAST`, so its first row is the
    resume course; `load_learner_curriculum` already returns ordered items with
    per-lecture `is_completed` and per-section `is_locked` from the cohort and
    drip gates. Returns None when the learner has no active enrollment.
    """
    # Default scope is active-only on purpose: My Courses additionally lists
    # courses the learner completed and then unenrolled from, but resuming one
    # would send them to content they no longer have access to.
    enrollment = get_learner_enrollments(user).first()
    if enrollment is None:
        return None

    curriculum = load_learner_curriculum(
        enrollment.course, user, is_instructor=False, enrollment=enrollment,
    )
    item, section, locked_until = _first_incomplete_lecture(curriculum)

    next_lecture = None
    if item is not None:
        next_lecture = {
            'lecture_id': item['object_id'],
            'content_id': item['content_id'],
            'title': item['title'],
            'lecture_type': item['lecture_type'],
            'duration_seconds': item.get('duration_seconds'),
            'section': {
                'id': section['id'],
                'title': section['title'],
                'position': section['position'],
            },
        }

    return {
        'enrollment': {
            'id': enrollment.pk,
            'progress_percent': enrollment.progress_percent,
            'last_accessed_at': enrollment.last_accessed_at,
            'completed_at': enrollment.completed_at,
        },
        'course': _course_ref(enrollment.course),
        'next_lecture': next_lecture,
        'is_course_complete': enrollment.completed_at is not None,
        'locked_until': locked_until if next_lecture is None else None,
    }
