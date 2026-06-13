import uuid

from django.db import models

from courses.all_models.course_models import TimestampedModel
from courses.all_models.enrollment_models import Enrollment


class Certificate(TimestampedModel):
    """
    Issued once per enrollment when the learner first reaches 100% progress.

    Fields are intentionally denormalized snapshots — the learner's name and
    the course title are frozen at issue time so the certificate remains an
    accurate historical record even if either changes later.

    certificate_uid is the public-facing identifier used in share/verify URLs.
    It is a UUID4 so it cannot be guessed or enumerated sequentially.
    """

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

    class Meta:
        db_table = 'course_certificates'
        verbose_name = 'Certificate'
        verbose_name_plural = 'Certificates'
        indexes = [
            models.Index(fields=['certificate_uid'], name='idx_cert_uid'),
        ]

    def __str__(self):
        return f'Certificate {self.certificate_uid} — {self.learner_name} / {self.course_title}'
