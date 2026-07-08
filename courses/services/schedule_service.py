import logging

from django.db.models import Q

from courses.all_models.course_models import NidusCourse
from courses.all_models.schedule_models import CourseSchedule

logger = logging.getLogger(__name__)


class ScheduleError(Exception):
    """Raised on course-schedule business-rule violations. Carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def _institution_profile_id(user):
    """The caller's partner-institution profile id, or None."""
    if user.user_type != 'partner_institution':
        return None
    profile = getattr(user, 'partner_institution_profile', None)
    return profile.id if profile else None


def get_course_for_schedule_manage(user, course_pk):
    """
    Fetch the course the caller may manage schedules on.

    Institution-owned course → only the owning institution; individual course →
    only its creator. No access → ScheduleError(404) with a non-leaking message
    (numeric-ID access-denied policy).
    """
    course = NidusCourse.objects.filter(pk=course_pk).first()
    if course is None:
        raise ScheduleError('Course not found.', http_status=404)

    if course.partner_institution_id is not None:
        if course.partner_institution_id != _institution_profile_id(user):
            raise ScheduleError('Course not found.', http_status=404)
    elif course.created_by_id != user.id:
        raise ScheduleError('Course not found.', http_status=404)

    return course


def get_course_for_schedule_read(user, course_pk):
    """
    Fetch the course the caller may view schedules on: manage-eligible callers
    plus the course's roster instructors (read-only visibility for experts).
    """
    try:
        return get_course_for_schedule_manage(user, course_pk)
    except ScheduleError:
        course = (
            NidusCourse.objects
            .filter(Q(pk=course_pk) & Q(instructors=user))
            .first()
        )
        if course is None:
            raise ScheduleError('Course not found.', http_status=404)
        return course


def get_schedule(course, schedule_id):
    """Fetch a schedule scoped to *course*; wrong-course or missing id → 404."""
    schedule = (
        course.schedules
        .select_related('created_by', 'last_edited_by')
        .filter(pk=schedule_id)
        .first()
    )
    if schedule is None:
        raise ScheduleError('Schedule not found.', http_status=404)
    return schedule


def get_course_schedules(course):
    """All schedules of a course for the list endpoint, newest first."""
    return (
        course.schedules
        .select_related('created_by', 'last_edited_by')
        .order_by('-created_at')
    )


def delete_schedule(schedule):
    """Hard-delete a schedule; permitted only while still a draft."""
    if schedule.status != CourseSchedule.Status.DRAFT:
        raise ScheduleError(
            'Only draft schedules can be deleted.',
            http_status=422,
        )
    schedule.delete()


def activate_schedule(schedule, actor):
    """draft → scheduled. Runs the activation completeness check."""
    schedule.transition_to(CourseSchedule.Status.SCHEDULED, actor=actor)
    return schedule


def archive_schedule(schedule, actor):
    """completed → archived."""
    schedule.transition_to(CourseSchedule.Status.ARCHIVED, actor=actor)
    return schedule


def rework_schedule(schedule, actor):
    """archived → draft, or scheduled → draft (pull back a premature activation)."""
    schedule.transition_to(CourseSchedule.Status.DRAFT, actor=actor)
    return schedule
