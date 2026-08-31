import uuid

from django.db import models

from authentication.utils.upload_helpers import certificate_signature_path
from courses.all_models.course_models import TimestampedModel
from courses.all_models.enrollment_models import Enrollment


class Certificate(TimestampedModel):
    """
    Issued once per enrollment when the learner first reaches 100% progress.

    Almost every field here is a denormalized snapshot frozen at issue time, so
    the certificate stays an accurate historical record even after the learner
    renames themselves, the course is retitled, or a signatory changes their
    signature. The signature images are *copied* rather than referenced for the
    same reason — see issue_certificate() in services/certificate_service.py.

    Two public identifiers:
      certificate_uid  UUID4, unguessable, used by the original share/verify URLs.
      certificate_id   Human-readable credential number (CC-2026-NEXT-000123)
                       printed on the certificate and used for verification.
    """

    class Status(models.TextChoices):
        VALID = 'valid', 'Valid'
        REVOKED = 'revoked', 'Revoked'

    enrollment = models.OneToOneField(
        Enrollment,
        on_delete=models.CASCADE,
        related_name='certificate',
    )
    certificate_uid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
        help_text='Public-facing UUID used in share/verify URLs.',
    )
    certificate_id = models.CharField(
        max_length=40,
        unique=True,
        null=True,
        db_index=True,
        help_text='Human-readable credential ID, e.g. CC-2026-NEXT-000123. '
                  'Permanent — never regenerated after issuance.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.VALID,
        db_index=True,
    )
    revoked_at = models.DateTimeField(blank=True, null=True)
    revoked_reason = models.TextField(blank=True, default='')

    learner_name = models.CharField(
        max_length=200,
        help_text='Snapshot of the learner full_name at time of issue.',
    )
    course_title = models.CharField(
        max_length=200,
        help_text='Snapshot of the course title at time of issue.',
    )
    issued_at = models.DateTimeField(
        help_text='Timestamp when the certificate was issued.',
    )

    # ── Course snapshot ──
    completion_date = models.DateField(
        blank=True, null=True,
        help_text='Date the learner completed the course.',
    )
    course_duration = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Human-readable duration at issue time, e.g. "12 Weeks". '
                  'Blank for self-paced courses with no cohort schedule.',
    )
    learning_hours = models.PositiveIntegerField(
        default=0,
        help_text='Snapshot of NidusCourse.learning_hours.',
    )

    # ── Instructor snapshot ──
    instructor_name = models.CharField(max_length=200, blank=True, default='')
    instructor_designation = models.CharField(max_length=200, blank=True, default='')
    instructor_signature = models.ImageField(
        upload_to=certificate_signature_path, blank=True, null=True,
        help_text='Frozen copy of the instructor signature image.',
    )

    # ── Authorized signatory snapshot ──
    authorized_signatory_name = models.CharField(max_length=200, blank=True, default='')
    authorized_signatory_designation = models.CharField(max_length=200, blank=True, default='')
    authorized_signature = models.ImageField(
        upload_to=certificate_signature_path, blank=True, null=True,
        help_text='Frozen copy of the authorized signatory signature image.',
    )

    issuer_name = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Issuing organization at time of issue.',
    )

    class Meta:
        db_table = 'course_certificates'
        verbose_name = 'Certificate'
        verbose_name_plural = 'Certificates'
        indexes = [
            models.Index(fields=['certificate_uid'], name='idx_cert_uid'),
            models.Index(fields=['certificate_id'], name='idx_cert_public_id'),
        ]

    def __str__(self):
        return f'Certificate {self.certificate_id or self.certificate_uid} — {self.learner_name} / {self.course_title}'

    @property
    def is_valid(self) -> bool:
        return self.status == self.Status.VALID
