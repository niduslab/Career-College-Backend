import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

# Reuse the institution roster helper so the "active affiliated expert" rule
# is defined in exactly one place across courses and webinars.
from courses.services.institution_course_service import (
    InstitutionCourseError,
    _get_active_expert_user,
)

from webinars.models import Webinar

logger = logging.getLogger(__name__)


class WebinarError(Exception):
    """Raised on webinar business-rule violations. Carries an HTTP status."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


# ── Catalog ──────────────────────────────────────────────────────────────────

def get_catalog_webinars():
    """Published webinars for the public catalog, soonest first."""
    return (
        Webinar.objects
        .filter(is_published=True)
        .select_related('created_by', 'category', 'partner_institution', 'host_expert')
        .order_by('scheduled_at')
    )


def filter_catalog_webinars(queryset, params):
    """Apply optional catalog filters: ?category=<id>, ?upcoming=true."""
    category_id = params.get('category')
    if category_id:
        try:
            queryset = queryset.filter(category_id=int(category_id))
        except (TypeError, ValueError):
            raise DjangoValidationError({'category': 'category must be an integer id.'})

    if params.get('upcoming', '').lower() == 'true':
        from django.utils import timezone
        queryset = queryset.filter(scheduled_at__gte=timezone.now())

    return queryset


# ── Host assignment ────────────────────────────────────────────────────────--

def assign_webinar_host(webinar, institution_profile, expert_user_id):
    """Assign one of the institution's active experts as the webinar host."""
    if webinar.partner_institution_id != institution_profile.id:
        raise WebinarError('Webinar not found.', http_status=404)

    if not webinar.is_editable():
        raise WebinarError(
            'This webinar is locked and its host cannot be changed.',
            http_status=422,
        )

    try:
        expert_user = _get_active_expert_user(institution_profile, expert_user_id)
    except InstitutionCourseError as exc:
        # Re-wrap so callers only ever catch WebinarError.
        raise WebinarError(str(exc), http_status=exc.http_status)

    with transaction.atomic():
        webinar.host_expert = expert_user
        webinar.save(update_fields=['host_expert', 'updated_at'])

    return expert_user


def set_institutional_speakers(webinar, institution_profile, expert_user_ids):
    """
    Replace the webinar's institutional-speaker set with the given expert users.

    Each id must be an active affiliated expert of the owning institution (same
    rule as the host and the course roster). A foreign/inactive/unknown id → 422.
    Full replace (idempotent): passing [] clears the set.
    """
    if webinar.partner_institution_id != institution_profile.id:
        raise WebinarError('Webinar not found.', http_status=404)

    if not webinar.is_editable():
        raise WebinarError(
            'This webinar is locked and its speakers cannot be changed.',
            http_status=422,
        )

    experts = []
    seen = set()
    for uid in expert_user_ids:
        if uid in seen:
            continue
        seen.add(uid)
        try:
            experts.append(_get_active_expert_user(institution_profile, uid))
        except InstitutionCourseError as exc:
            raise WebinarError(str(exc), http_status=exc.http_status)

    with transaction.atomic():
        webinar.institutional_speakers.set(experts)

    return experts


def clear_webinar_host(webinar, institution_profile):
    """Remove the assigned host from a webinar."""
    if webinar.partner_institution_id != institution_profile.id:
        raise WebinarError('Webinar not found.', http_status=404)

    if not webinar.is_editable():
        raise WebinarError(
            'This webinar is locked and its host cannot be changed.',
            http_status=422,
        )

    if webinar.host_expert_id is None:
        raise WebinarError('This webinar has no assigned host.', http_status=422)

    with transaction.atomic():
        webinar.host_expert = None
        webinar.save(update_fields=['host_expert', 'updated_at'])
