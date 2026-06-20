import logging

from django.db import transaction

logger = logging.getLogger(__name__)


class InstitutionCourseError(Exception):
    """Raised on institution course-roster business-rule violations. Carries HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.http_status = http_status


def _get_active_expert_user(institution_profile, expert_user_id):
    """
    Resolve an active affiliated expert's User by id.

    Raises InstitutionCourseError(422) when the user is not an active expert of
    this institution.
    """
    from authentication.models import InstructorProfile

    profile = (
        InstructorProfile.objects
        .filter(
            user_id=expert_user_id,
            affiliated_institution=institution_profile,
            affiliation_status='active',
        )
        .select_related('user')
        .first()
    )
    if profile is None:
        raise InstitutionCourseError(
            'This user is not an active expert of your institution.',
            http_status=422,
        )
    return profile.user


def add_course_instructor(course, institution_profile, expert_user_id):
    """
    Add one of the institution's active experts to a course's instructor roster.

    Raises InstitutionCourseError on any validation failure.
    """
    if course.partner_institution_id != institution_profile.id:
        # Course not owned by this institution — caller already gets 404 upstream,
        # but guard here too.
        raise InstitutionCourseError('Course not found.', http_status=404)

    if not course.is_editable():
        raise InstitutionCourseError(
            'This course is locked and its roster cannot be changed.',
            http_status=422,
        )

    expert_user = _get_active_expert_user(institution_profile, expert_user_id)

    if course.instructors.filter(pk=expert_user.pk).exists():
        raise InstitutionCourseError(
            'This expert is already an instructor on this course.',
            http_status=422,
        )

    with transaction.atomic():
        course.instructors.add(expert_user)

    return expert_user


def remove_course_instructor(course, institution_profile, expert_user_id):
    """Remove an expert from a course's instructor roster."""
    if course.partner_institution_id != institution_profile.id:
        raise InstitutionCourseError('Course not found.', http_status=404)

    if not course.instructors.filter(pk=expert_user_id).exists():
        raise InstitutionCourseError(
            'This expert is not an instructor on this course.',
            http_status=422,
        )

    with transaction.atomic():
        course.instructors.remove(expert_user_id)
