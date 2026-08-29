import logging
import os
import re
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse
from courses.all_models.enrollment_models import Enrollment

logger = logging.getLogger(__name__)

# How many times to retry certificate_id generation when a concurrent issuance
# claims the same sequence number.
_ID_MAX_ATTEMPTS = 5

_ID_PREFIX = 'CC'
_ID_ABBREV_FALLBACK = 'GEN'
_ID_ABBREV_MAX_LEN = 6
_ID_SEQUENCE_WIDTH = 6


class CertificateError(Exception):
    """Business-rule violation in the certificate flow (mirrors ScheduleError)."""

    def __init__(self, message, http_status=422):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


#  Certificate ID

def _slug_abbrev(course) -> str:
    """First alphanumeric token of the course slug, uppercased and truncated."""
    slug = (getattr(course, 'slug', '') or '').strip()
    for token in re.split(r'[^A-Za-z0-9]+', slug):
        cleaned = token.upper()
        if cleaned:
            return cleaned[:_ID_ABBREV_MAX_LEN]
    return _ID_ABBREV_FALLBACK


def _certificate_id_prefix(course, year) -> str:
    return f'{_ID_PREFIX}-{year}-{_slug_abbrev(course)}-'


def _next_certificate_id(course, year) -> str:
    """Next sequential id for this (year, course-abbrev) bucket.

    Not race-free on its own — the unique constraint is the real guard, and the
    caller retries on IntegrityError. Issuance happens once per course
    completion, so contention is rare enough that a dedicated DB sequence is
    not worth the DDL.
    """
    prefix = _certificate_id_prefix(course, year)
    used = Certificate.objects.filter(
        certificate_id__startswith=prefix).count()
    return f'{prefix}{used + 1:0{_ID_SEQUENCE_WIDTH}d}'


def build_verification_url(certificate) -> str:
    """Absolute, frontend-facing URL a QR code can encode.

    Distinct from the relative ``verify_url`` API path on the learner list
    serializer. FRONTEND_URL must point at the production domain in production —
    a localhost value here ends up printed on the PDF.
    """
    base = getattr(settings, 'FRONTEND_URL', '').rstrip('/')
    path = getattr(settings, 'CERTIFICATE_VERIFY_PATH', '/verify/')
    identifier = certificate.certificate_id or certificate.certificate_uid
    return f'{base}{path}{identifier}'


# Snapshot resolution

def _resolve_instructor(course):
    """(name, designation, signature_field_or_None) for the course's instructor."""
    user = course.instructors.first() or course.created_by
    if user is None:
        return '', '', None

    profile = getattr(user, 'instructor_profile', None)
    designation = ''
    signature = None
    if profile is not None:
        designation = (profile.current_title or profile.headline or '').strip()
        signature = profile.signature or None

    return (
        (user.full_name or '').strip(),
        designation or 'Course Instructor',
        signature,
    )


def _resolve_authorized_signatory(course):
    """(name, designation, signature_or_None, issuer_name) with institution → platform fallback.

    An institution that has not configured a signatory falls through to the
    platform default rather than producing a blank signature block.
    """
    institution = getattr(course, 'partner_institution', None)
    if institution is not None and (institution.authorized_signatory_name or '').strip():
        return (
            institution.authorized_signatory_name.strip(),
            (institution.authorized_signatory_designation or '').strip(),
            institution.authorized_signature or None,
            (institution.institution_name or '').strip(),
        )

    # Imported lazily: courses must not import admin_console at module load.
    from admin_console.all_models.platform_settings_models import PlatformSettings

    platform = PlatformSettings.load()
    issuer = (platform.organization_name or '').strip()
    if institution is not None:
        # Institution owns the course but has no signatory of its own — keep the
        # institution as the issuer, borrow the platform's signatory.
        issuer = (institution.institution_name or '').strip() or issuer

    return (
        (platform.authorized_signatory_name or '').strip(),
        (platform.authorized_signatory_designation or '').strip(),
        platform.authorized_signature or None,
        issuer,
    )


def _resolve_course_duration(enrollment) -> str:
    """Human-readable duration, e.g. "12 Weeks". Blank when there is no cohort."""
    schedule = getattr(enrollment, 'schedule', None)
    if schedule is None or schedule.start_date is None or schedule.end_date is None:
        return ''
    days = (schedule.end_date - schedule.start_date).days
    if days <= 0:
        return ''
    weeks = max(1, round(days / 7))
    return f'{weeks} Week' if weeks == 1 else f'{weeks} Weeks'


def _copy_signature(certificate, field_name, source):
    """Copy a signature image onto the certificate so it is frozen at issuance.

    A reference would not survive the source being re-uploaded — ImageField.save()
    replaces the object at that storage key. Storage-agnostic: reads through the
    FieldFile, never .path(), so it works on S3.

    Best-effort — a failed copy leaves the field blank and is logged; it must
    never cost the learner their certificate.
    """
    if not source:
        return
    try:
        source.open('rb')
        try:
            data = source.read()
        finally:
            source.close()
        getattr(certificate, field_name).save(
            os.path.basename(source.name), ContentFile(data), save=False
        )
    except Exception:
        logger.warning(
            'Certificate signature copy failed: field=%s source=%s',
            field_name, getattr(source, 'name', '?'), exc_info=True,
        )


def _build_snapshot(enrollment) -> dict:
    """Every frozen field for a new certificate, resolved from live data."""
    course = enrollment.course
    completed_at = enrollment.completed_at
    instructor_name, instructor_designation, instructor_sig = _resolve_instructor(
        course)
    signatory_name, signatory_designation, signatory_sig, issuer = (
        _resolve_authorized_signatory(course)
    )

    return {
        'values': {
            'learner_name': enrollment.user.full_name,
            'course_title': course.title,
            'issued_at': completed_at or timezone.now(),
            'completion_date': completed_at.date() if completed_at else None,
            'course_duration': _resolve_course_duration(enrollment),
            'learning_hours': course.learning_hours,
            'instructor_name': instructor_name,
            'instructor_designation': instructor_designation,
            'authorized_signatory_name': signatory_name,
            'authorized_signatory_designation': signatory_designation,
            'issuer_name': issuer,
        },
        'instructor_signature': instructor_sig,
        'authorized_signature': signatory_sig,
    }


# Issuance

def issue_certificate(enrollment: Enrollment) -> Certificate:
    """Idempotent: create a Certificate for a completed enrollment if none exists.

    Every snapshot field — including copies of the two signature images — is
    written only on first creation, so a later signature or title change never
    alters an already-issued certificate.

    Raises CertificateError(422) when the enrollment is not complete. The only
    production caller already gates on completion; this guard stops any future
    caller from minting a certificate the learner did not earn.
    """
    existing = Certificate.objects.filter(enrollment=enrollment).first()
    if existing is not None:
        return existing

    if enrollment.completed_at is None:
        raise CertificateError('Learner has not completed this course.', 422)

    snapshot = _build_snapshot(enrollment)
    year = snapshot['values']['issued_at'].year

    for attempt in range(_ID_MAX_ATTEMPTS):
        certificate = Certificate(
            enrollment=enrollment,
            certificate_id=_next_certificate_id(enrollment.course, year),
            **snapshot['values'],
        )
        _copy_signature(certificate, 'instructor_signature',
                        snapshot['instructor_signature'])
        _copy_signature(certificate, 'authorized_signature',
                        snapshot['authorized_signature'])
        try:
            with transaction.atomic():
                certificate.save()
        except IntegrityError:
            # Either another worker issued this enrollment's certificate first,
            # or it claimed our sequence number. Both are recoverable.
            existing = Certificate.objects.filter(
                enrollment=enrollment).first()
            if existing is not None:
                return existing
            if attempt == _ID_MAX_ATTEMPTS - 1:
                raise
            continue

        logger.info(
            'Certificate issued: id=%s uid=%s user=%s course=%s',
            certificate.certificate_id, certificate.certificate_uid,
            enrollment.user_id, enrollment.course_id,
        )
        return certificate

    # Unreachable: the final attempt either returns or re-raises.
    raise CertificateError('Could not allocate a certificate ID.', 500)


# Lookups

_PUBLIC_SELECT_RELATED = (
    'enrollment__user',
    'enrollment__course',
    'enrollment__course__created_by',
    'enrollment__course__partner_institution',
)


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


def get_learner_certificates(user):
    """A learner's certificates, newest first, with the course joined.

    The explicit order_by is required: Certificate.Meta declares no `ordering`,
    and paginating an unordered queryset warns and can skip or duplicate rows
    across pages.
    """
    return (
        Certificate.objects
        .filter(enrollment__user=user)
        .select_related('enrollment__course')
        .order_by('-issued_at', '-id')
    )


def get_certificate_by_uid(certificate_uid) -> Certificate:
    """Public lookup by UUID. Raises Certificate.DoesNotExist if not found."""
    return Certificate.objects.select_related(*_PUBLIC_SELECT_RELATED).get(
        certificate_uid=certificate_uid
    )


def get_certificate_by_public_id(identifier) -> Certificate:
    """Public lookup by either UUID or human-readable certificate_id.

    One endpoint accepts both so a learner can paste whichever identifier they
    have. Raises Certificate.DoesNotExist if neither matches.
    """
    raw = str(identifier).strip()
    try:
        return get_certificate_by_uid(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        pass
    except Certificate.DoesNotExist:
        raise

    return Certificate.objects.select_related(*_PUBLIC_SELECT_RELATED).get(
        certificate_id__iexact=raw
    )


# Revocation

def _log_certificate_action(actor, action, certificate, reason=''):
    """Audit row for a certificate status change. Call inside the transaction."""
    from admin_console.services.user_admin_service import log_admin_action

    log_admin_action(
        actor=actor,
        action=action,
        target=certificate.enrollment.user,
        reason=reason,
        metadata={
            'certificate_id': certificate.certificate_id,
            'certificate_uid': str(certificate.certificate_uid),
            'course_title': certificate.course_title,
        },
    )


@transaction.atomic
def revoke_certificate(certificate_uid, *, actor, reason=''):
    """Mark a certificate revoked. Admin-only; the original snapshot is untouched.

    Revocation changes only the verification verdict — never the issued record,
    so the certificate stays an accurate account of what was awarded and when.
    """
    certificate = (
        Certificate.objects.select_for_update()
        .select_related('enrollment__user')
        .get(certificate_uid=certificate_uid)
    )
    if certificate.status == Certificate.Status.REVOKED:
        raise CertificateError('Certificate is already revoked.', 422)

    certificate.status = Certificate.Status.REVOKED
    certificate.revoked_at = timezone.now()
    certificate.revoked_reason = reason or ''
    certificate.save(
        update_fields=['status', 'revoked_at', 'revoked_reason', 'updated_at'])

    _log_certificate_action(
        actor, 'certificate_revoke', certificate, reason=reason,
    )
    logger.warning(
        'Certificate revoked: id=%s by=%s', certificate.certificate_id, getattr(
            actor, 'id', None)
    )
    return certificate


@transaction.atomic
def restore_certificate(certificate_uid, *, actor):
    """Lift a revocation, returning the certificate to valid."""
    certificate = (
        Certificate.objects.select_for_update()
        .select_related('enrollment__user')
        .get(certificate_uid=certificate_uid)
    )
    if certificate.status != Certificate.Status.REVOKED:
        raise CertificateError('Certificate is not revoked.', 422)

    certificate.status = Certificate.Status.VALID
    certificate.revoked_at = None
    certificate.revoked_reason = ''
    certificate.save(
        update_fields=['status', 'revoked_at', 'revoked_reason', 'updated_at'])

    _log_certificate_action(actor, 'certificate_restore', certificate)
    logger.warning(
        'Certificate restored: id=%s by=%s', certificate.certificate_id, getattr(
            actor, 'id', None)
    )
    return certificate
