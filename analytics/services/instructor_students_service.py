"""
Instructor student roster — the list behind the KPI counts.

`instructor_analytics_service` answers "how many students do I have"; this
module answers "who are they". Rows are per-enrollment, not per-learner, so a
learner in three of the instructor's courses appears three times with three
progress values. See docs/architecture/30-instructor-students.md.

Every field maps to a column that already exists. The single derived field is
`status`, whose rule lives in `derive_status()` and is mirrored exactly by
`_status_filter_q()` so SQL filtering and Python derivation cannot drift.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Exists, OuterRef, Q
from django.utils import timezone

from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse
from courses.all_models.enrollment_models import Enrollment

# A learner who hasn't opened the course in this long reads as "inactive".
# Product threshold, not a measurement — the API echoes it back so the
# frontend never keeps its own copy.
INACTIVE_AFTER_DAYS = 14
_DEFAULT_WINDOW_DAYS = 30
_MIN_SEARCH_LENGTH = 2
_TOP_COURSES_LIMIT = 5

STATUS_ACTIVE = 'active'
STATUS_INACTIVE = 'inactive'
STATUS_COMPLETED = 'completed'
STATUS_NOT_STARTED = 'not_started'
STATUS_UNENROLLED = 'unenrolled'

STATUSES = (
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    STATUS_COMPLETED,
    STATUS_NOT_STARTED,
    STATUS_UNENROLLED,
)

# Whitelisted sorts. Every entry carries an 'id' tiebreaker at query time:
# last_accessed_at is null for everyone who never opened the course, and equal
# sort keys let the paginator skip or repeat rows across pages.
_SORT_WHITELIST = {
    '-last_active': ('-last_accessed_at',),
    'last_active': ('last_accessed_at',),
    '-enrolled': ('-created_at',),
    'enrolled': ('created_at',),
    '-progress': ('-progress_percent',),
    'progress': ('progress_percent',),
    'name': ('user__full_name',),
}
_DEFAULT_SORT = '-last_active'


class InstructorStudentsError(Exception):
    """Mirrors ScheduleError / AdminUserActionError — carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _instructor_enrollment_scope(instructor):
    """Enrollments on courses the instructor owns, by either ownership path.

    `.distinct()` is applied by the caller and is mandatory — a course where
    the instructor is both a roster member and `created_by` would double every
    row it contributes.
    """
    return Q(course__instructors=instructor) | Q(course__created_by=instructor)


def _base_queryset(instructor):
    return Enrollment.objects.filter(_instructor_enrollment_scope(instructor)).distinct()


def derive_status(enrollment, inactive_cutoff):
    """Order matters: a learner who finished and then stopped opening the
    course is `completed`, not `inactive`."""
    if enrollment.completed_at is not None:
        return STATUS_COMPLETED
    if not enrollment.is_active:
        return STATUS_UNENROLLED
    if enrollment.last_accessed_at is None:
        return STATUS_NOT_STARTED
    if enrollment.last_accessed_at < inactive_cutoff:
        return STATUS_INACTIVE
    return STATUS_ACTIVE


def _status_filter_q(status, inactive_cutoff):
    """SQL twin of derive_status(). Filtering must happen in the database —
    filtering a serialized page instead would make the paginator's count
    describe a different set than the rows returned."""
    completed = Q(completed_at__isnull=False)
    if status == STATUS_COMPLETED:
        return completed
    if status == STATUS_UNENROLLED:
        return ~completed & Q(is_active=False)
    if status == STATUS_NOT_STARTED:
        return ~completed & Q(is_active=True) & Q(last_accessed_at__isnull=True)
    if status == STATUS_INACTIVE:
        return ~completed & Q(is_active=True) & Q(last_accessed_at__lt=inactive_cutoff)
    if status == STATUS_ACTIVE:
        return ~completed & Q(is_active=True) & Q(last_accessed_at__gte=inactive_cutoff)
    raise InstructorStudentsError(f'Unknown status: {status}')


def _resolve_owned_course(instructor, course_id):
    """A course_id the caller doesn't own is a 400, not an empty list — an
    empty list would look like "you have no students there" and hide the
    mistake."""
    try:
        course_id = int(course_id)
    except (TypeError, ValueError):
        raise InstructorStudentsError('course_id must be an integer.')

    owns = (
        NidusCourse.objects
        .filter(Q(instructors=instructor) | Q(created_by=instructor), pk=course_id)
        .exists()
    )
    if not owns:
        raise InstructorStudentsError('Course not found.', http_status=404)
    return course_id


def build_student_queryset(instructor, params):
    """Filtered + sorted enrollment rows for the roster table.

    `params` is request.query_params (or any mapping). Unknown filter values
    raise InstructorStudentsError rather than being silently ignored.
    """
    inactive_cutoff = timezone.now() - timedelta(days=INACTIVE_AFTER_DAYS)
    qs = _base_queryset(instructor)

    search = (params.get('search') or '').strip()
    if search:
        if len(search) < _MIN_SEARCH_LENGTH:
            raise InstructorStudentsError(
                f'Search term must be at least {_MIN_SEARCH_LENGTH} characters.'
            )
        qs = qs.filter(
            Q(user__full_name__icontains=search) | Q(user__email__icontains=search)
        )

    course_id = params.get('course_id')
    if course_id not in (None, '', 'all'):
        qs = qs.filter(course_id=_resolve_owned_course(instructor, course_id))

    status = (params.get('status') or '').strip()
    if status and status != 'all':
        if status not in STATUSES:
            raise InstructorStudentsError(
                f'Invalid status. Choose one of: {", ".join(STATUSES)}.'
            )
        qs = qs.filter(_status_filter_q(status, inactive_cutoff))

    sort = (params.get('sort') or _DEFAULT_SORT).strip()
    if sort not in _SORT_WHITELIST:
        raise InstructorStudentsError(
            f'Invalid sort. Choose one of: {", ".join(_SORT_WHITELIST)}.'
        )

    qs = qs.select_related(
        'user', 'user__learner_profile', 'course', 'schedule',
    ).annotate(
        # A revoked certificate reads as "no certificate" on the roster.
        has_certificate=Exists(Certificate.objects.filter(
            enrollment=OuterRef('pk'), status=Certificate.Status.VALID,
        )),
    )

    # 'id' tiebreaker keeps pagination stable across equal sort keys.
    return qs.order_by(*_SORT_WHITELIST[sort], 'id')


def _avatar_url(user):
    """Media-root-relative path, not absolute. The frontend's `mediaUrl()`
    prepends the API origin it already knows; building an absolute URL here
    would bake in whatever Host header the request arrived with.

    LearnerProfile is signal-created, but legacy rows predate the signal —
    getattr rather than a bare attribute access.
    """
    profile = getattr(user, 'learner_profile', None)
    if profile is None or not profile.profile_photo:
        return None
    return profile.profile_photo.url


def serialize_student_row(enrollment, inactive_cutoff):
    """The analytics app has no serializer layer — every endpoint returns plain
    dicts built here. Keeps the shape next to the query that feeds it."""
    user = enrollment.user
    schedule = enrollment.schedule
    return {
        'enrollment_id': enrollment.id,
        'student': {
            'id': user.id,
            'full_name': user.full_name,
            'email': user.email,
            'avatar': _avatar_url(user),
        },
        'course': {
            'id': enrollment.course_id,
            'title': enrollment.course.title,
            'slug': enrollment.course.slug,
        },
        'cohort': schedule.cohort_label if schedule else None,
        'progress_percent': enrollment.progress_percent,
        'status': derive_status(enrollment, inactive_cutoff),
        'enrollment_type': enrollment.enrollment_type,
        'enrolled_at': enrollment.created_at,
        'last_active_at': enrollment.last_accessed_at,
        'completed_at': enrollment.completed_at,
        'has_certificate': getattr(enrollment, 'has_certificate', False),
    }


def serialize_student_page(enrollments):
    inactive_cutoff = timezone.now() - timedelta(days=INACTIVE_AFTER_DAYS)
    return [serialize_student_row(e, inactive_cutoff) for e in enrollments]


def _top_courses_by_students(instructor, limit=_TOP_COURSES_LIMIT):
    rows = (
        NidusCourse.objects
        .filter(Q(instructors=instructor) | Q(created_by=instructor))
        .distinct()
        .annotate(students=Count('enrollments__user', distinct=True))
        .filter(students__gt=0)
        .order_by('-students', 'id')
        .values('id', 'title', 'slug', 'students')[:limit]
    )
    return list(rows)


def students_summary(instructor, window_days=_DEFAULT_WINDOW_DAYS):
    """Roster-wide KPIs. Deliberately separate from the paginated list: these
    describe every student, not the current page. A frontend counting rows in
    one page would cap out at page_size and silently under-report."""
    now = timezone.now()
    inactive_cutoff = now - timedelta(days=INACTIVE_AFTER_DAYS)
    base = _base_queryset(instructor)

    # Distinct learners — someone enrolled in three courses is one student.
    total_students = base.values('user_id').distinct().count()
    active_students = (
        base.filter(_status_filter_q(STATUS_ACTIVE, inactive_cutoff))
        .values('user_id').distinct().count()
    )

    # Average over active enrollments only; abandoned rows would drag it down.
    avg_progress = base.filter(is_active=True).aggregate(v=Avg('progress_percent'))['v']
    avg_progress = round(float(avg_progress), 1) if avg_progress is not None else 0.0

    window_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=window_days * 2)
    new_this_period = base.filter(created_at__gte=window_start, created_at__lt=now).count()
    previous = base.filter(created_at__gte=prev_start, created_at__lt=window_start).count()
    new_growth_pct = (
        round((new_this_period - previous) / previous * 100, 1) if previous else None
    )

    # Enrollment-row counts, not distinct learners — the key names say so.
    status_breakdown = {
        status: base.filter(_status_filter_q(status, inactive_cutoff)).count()
        for status in STATUSES
    }

    return {
        'total_students': total_students,
        'active_students': active_students,
        'avg_progress': avg_progress,
        'new_this_period': new_this_period,
        'new_growth_pct': new_growth_pct,
        'window_days': window_days,
        'inactive_after_days': INACTIVE_AFTER_DAYS,
        'status_breakdown': status_breakdown,
        'top_courses': _top_courses_by_students(instructor),
    }


def instructor_course_options(instructor):
    """Courses for the roster's course-filter dropdown. Every owned course is
    listed, including zero-enrollment ones — the instructor should be able to
    select a course and see that it has no students yet."""
    rows = (
        NidusCourse.objects
        .filter(Q(instructors=instructor) | Q(created_by=instructor))
        .distinct()
        .order_by('title', 'id')
        .values('id', 'title', 'slug')
    )
    return list(rows)
