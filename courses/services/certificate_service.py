import logging

from django.utils import timezone

from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse
from courses.all_models.enrollment_models import Enrollment

logger = logging.getLogger(__name__)


def issue_certificate(enrollment: Enrollment) -> Certificate:
    """Idempotent: create a Certificate for a completed enrollment if none exists.

    Uses get_or_create on the OneToOne field so repeated calls (e.g. on_commit
    fired twice, Celery retry) return the existing row without modification.
    The learner_name and course_title snapshots are only written on first creation.
    """
    certificate, created = Certificate.objects.get_or_create(
        enrollment=enrollment,
        defaults={
            'learner_name': enrollment.user.full_name,
            'course_title': enrollment.course.title,
            'issued_at': enrollment.completed_at or timezone.now(),
        },
    )
    if created:
        logger.info(
            'Certificate issued: uid=%s user=%s course=%s',
            certificate.certificate_uid,
            enrollment.user_id,
            enrollment.course_id,
        )
    return certificate


def get_certificate_for_learner(user, course_slug: str) -> Certificate:
    """Fetch a learner's certificate by course slug.

    Raises:
        NidusCourse.DoesNotExist  — course slug not found (caller → 404)
        PermissionError           — user not enrolled (caller → 403, slug policy)
        Certificate.DoesNotExist  — enrolled but course not yet completed (caller → 404)
    """
    course = NidusCourse.objects.get(slug=course_slug)
    enrollment = Enrollment.objects.filter(user=user, course=course).first()
    if enrollment is None:
        raise PermissionError('Not enrolled.')
    return Certificate.objects.get(enrollment=enrollment)


def get_certificate_by_uid(certificate_uid) -> Certificate:
    """Public lookup by UUID. Raises Certificate.DoesNotExist if not found."""
    return Certificate.objects.select_related(
        'enrollment__user',
        'enrollment__course',
        'enrollment__course__created_by',
    ).get(certificate_uid=certificate_uid)
