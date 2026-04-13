import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from id_verification.utils import (
    id_document_front_path,
    id_document_back_path,
    resume_path,
    selfie_path,
)

logger = logging.getLogger(__name__)


class IdentityVerification(models.Model):
    """
    Tracks an instructor's identity verification request through its lifecycle.

    Workflow:
        draft → submitted → under_review → approved / rejected / action_required
        action_required → submitted  (instructor resubmits)
        submitted / under_review / action_required → expired  (admin / system)
    """

    # ── Relationships ──
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='identity_verifications',
        help_text='Instructor who submitted the verification request',
    )

    # ── Document type ──
    DOCUMENT_TYPE_CHOICES = (
        ('national_id', 'National ID Card'),
        ('passport', 'Passport'),
        ('drivers_license', "Driver's License"),
        ('residence_permit', 'Residence Permit'),
    )
    document_type = models.CharField(
        max_length=20,
        choices=DOCUMENT_TYPE_CHOICES,
        blank=True,
        default='',
        help_text='Type of identity document submitted',
    )

    # ── Document details ──
    document_number = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='ID/passport/licence number',
    )
    issuing_country = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='Country that issued the document',
    )
    expiry_date = models.DateField(
        blank=True,
        null=True,
        help_text='Document expiry date (if applicable)',
    )

    # ── Uploaded files ──
    document_front = models.ImageField(
        upload_to=id_document_front_path,
        blank=True,
        help_text='Front side of the identity document',
    )
    document_back = models.ImageField(
        upload_to=id_document_back_path,
        blank=True,
        null=True,
        help_text='Back side of the identity document (if applicable)',
    )
    selfie = models.ImageField(
        upload_to=selfie_path,
        blank=True,
        help_text='Selfie holding the identity document',
    )
    resume = models.FileField(
        upload_to=resume_path,
        blank=True,
        null=True,
        help_text='Resume / CV document (PDF recommended)',
    )

    # ── Status lifecycle ──
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('under_review', 'Under Review'),
        ('action_required', 'Action Required'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        db_index=True,
    )

    # ── Review fields (populated by admin) ──
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_verifications',
        help_text='Admin who reviewed this request',
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(
        blank=True,
        default='',
        help_text='Reason for rejection (required when rejecting)',
    )
    action_required_reason = models.TextField(
        blank=True,
        default='',
        help_text='What the instructor needs to fix (set by admin)',
    )
    admin_notes = models.TextField(
        blank=True,
        default='',
        help_text='Internal notes visible only to admins',
    )

    # ── Timestamps ──
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text='When the instructor submitted (or resubmitted) the request',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'instructor_identity_verification'
        verbose_name = 'Identity Verification'
        verbose_name_plural = 'Identity Verifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status'], name='idx_idv_user_status'),
            models.Index(fields=['status', '-created_at'], name='idx_idv_status_date'),
            models.Index(fields=['reviewed_by'], name='idx_idv_reviewer'),
        ]

    # ── Valid transitions ──
    VALID_TRANSITIONS = {
        'draft': ('submitted',),
        'submitted': ('under_review', 'expired'),
        'under_review': ('approved', 'rejected', 'action_required', 'expired'),
        'action_required': ('submitted', 'expired'),
    }

    # Fields required before leaving draft.
    REQUIRED_FOR_SUBMIT = (
        'document_type', 'document_number', 'issuing_country',
        'document_front', 'selfie',
    )

    def clean(self):
        super().clean()
        if self.user.user_type != 'instructor':
            raise ValidationError('Identity verification is only available for instructors.')
        if self.expiry_date and self.expiry_date < timezone.now().date():
            raise ValidationError({'expiry_date': 'Document has already expired.'})

    def _validate_completeness(self):
        """Ensure all required fields are filled before submission."""
        missing = [f for f in self.REQUIRED_FOR_SUBMIT if not getattr(self, f)]
        if missing:
            raise ValidationError(
                {f: 'This field is required before submitting.' for f in missing}
            )

    def transition_to(self, new_status, reviewer=None, rejection_reason='',
                      action_required_reason='', admin_notes=''):
        """
        Move the verification to *new_status* with guard-rail checks.
        Raises ``ValidationError`` on illegal transitions.
        """
        allowed = self.VALID_TRANSITIONS.get(self.status, ())
        if new_status not in allowed:
            raise ValidationError(
                f'Cannot transition from "{self.status}" to "{new_status}". '
                f'Allowed: {", ".join(allowed) if allowed else "none (terminal state)"}.'
            )

        # Submission completeness check.
        if new_status == 'submitted':
            self._validate_completeness()

        if new_status == 'rejected' and not rejection_reason:
            raise ValidationError({'rejection_reason': 'A reason is required when rejecting.'})

        if new_status == 'action_required' and not action_required_reason:
            raise ValidationError(
                {'action_required_reason': 'A reason is required when requesting action.'}
            )

        if new_status in ('approved', 'rejected', 'action_required') and reviewer is None:
            raise ValidationError('A reviewer is required for this transition.')

        self.status = new_status
        self.admin_notes = admin_notes or self.admin_notes

        if new_status == 'submitted':
            self.submitted_at = timezone.now()

        if new_status in ('under_review', 'approved', 'rejected', 'action_required'):
            self.reviewed_by = reviewer
            self.reviewed_at = timezone.now()

        if new_status == 'rejected':
            self.rejection_reason = rejection_reason

        if new_status == 'action_required':
            self.action_required_reason = action_required_reason

        self.save()

        # Side-effect: mark the instructor profile as verified on approval.
        if new_status == 'approved':
            self._mark_instructor_verified()

        logger.info(
            'Verification %s for user %s transitioned to %s by %s',
            self.pk, self.user_id, new_status,
            reviewer.email if reviewer else 'system',
        )

    def _mark_instructor_verified(self):
        """Set the instructor profile's is_verified flag to True."""
        profile = getattr(self.user, 'instructor_profile', None)
        if profile and not profile.is_verified:
            profile.is_verified = True
            profile.save(update_fields=['is_verified', 'updated_at'])

    def __str__(self):
        return f"Verification #{self.pk} — {self.user.full_name} ({self.get_status_display()})"
