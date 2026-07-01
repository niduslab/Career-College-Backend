import logging
import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from authentication.models import PartnerInstitutionProfile
from courses.all_models.course_models import AuthoredModel, CourseCategory

logger = logging.getLogger(__name__)


def webinar_thumbnail_upload_path(instance, filename):
    """Generate deterministic, URL-safe upload path for webinar thumbnails."""
    base_name, ext = os.path.splitext(filename)
    slug = slugify(base_name) or 'thumbnail'
    unique_suffix = uuid.uuid4().hex[:10]
    return f"webinars/thumbnails/{slug}_{unique_suffix}{ext.lower()}"


webinar_thumbnail_upload_path.__module__ = 'webinars.models'


class Webinar(AuthoredModel):
    """
    A live webinar published by a partner institution.

    Parallels ``NidusCourse`` (same review state machine), but it is metadata +
    an external meeting link rather than a curriculum tree. Presenters live on
    this one model: a single assigned ``host_expert`` (a platform user) plus an
    inline ``guest_speakers`` list for external presenters with no account.
    """

    class WebinarStatus(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED = 'archived', 'Archived'

    class MeetingProvider(models.TextChoices):
        ZOOM = 'zoom', 'Zoom'
        MEET = 'meet', 'Google Meet'
        JITSI = 'jitsi', 'Jitsi'
        OTHER = 'other', 'Other'

    # ── Ownership / presenters ──
    partner_institution = models.ForeignKey(
        PartnerInstitutionProfile,
        on_delete=models.SET_NULL,
        related_name='webinars',
        blank=True,
        null=True,
        help_text='Partner institution that owns this webinar. Set automatically at creation.',
    )
    host_expert = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='hosted_webinars',
        blank=True,
        null=True,
        help_text='Assigned platform-expert host. Set via the host endpoint (active affiliated expert).',
    )
    institutional_speakers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='speaking_webinars',
        blank=True,
        help_text='Active affiliated experts of the owning institution credited as speakers. Credit-only — no authoring rights.',
    )
    guest_speakers = models.JSONField(
        default=list,
        blank=True,
        help_text='External presenters without a platform account: list of {full_name, title, bio}.',
    )
    category = models.ForeignKey(
        CourseCategory,
        on_delete=models.SET_NULL,
        related_name='webinars',
        blank=True,
        null=True,
        db_index=True,
    )

    # ── Definition ──
    title = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=280, unique=True, db_index=True)
    description = models.TextField(blank=True, default='')
    thumbnail = models.ImageField(upload_to=webinar_thumbnail_upload_path, blank=True, null=True)
    scheduled_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text='Webinar start time, stored in UTC.',
    )
    timezone = models.CharField(
        max_length=64,
        default='UTC',
        help_text='Display time zone for the scheduled time, e.g. "Asia/Dhaka".',
    )
    duration_minutes = models.PositiveIntegerField(default=0)
    max_capacity = models.PositiveIntegerField(
        blank=True,
        null=True,
        help_text='Maximum number of registrations. Null = unlimited.',
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    # ── Live delivery (external provider) ──
    meeting_provider = models.CharField(
        max_length=10,
        choices=MeetingProvider.choices,
        default=MeetingProvider.OTHER,
    )
    meeting_url = models.URLField(
        blank=True,
        null=True,
        help_text='External meeting join link. Exposed only to registrants.',
    )

    # ── Review lifecycle ──
    status = models.CharField(
        max_length=20,
        choices=WebinarStatus.choices,
        default=WebinarStatus.DRAFT,
        db_index=True,
    )
    is_published = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Denormalized flag for fast published-webinar queries.',
    )
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'webinars'
        verbose_name = 'Webinar'
        verbose_name_plural = 'Webinars'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='idx_webinar_status_date'),
            models.Index(fields=['is_published', 'scheduled_at'], name='idx_webinar_pub_sched'),
            models.Index(fields=['partner_institution', 'status'], name='idx_webinar_inst_status'),
        ]

    def __str__(self):
        return f'{self.title} ({self.get_status_display()})'

    def clean(self):
        super().clean()
        if self.created_by and self.created_by.user_type != 'partner_institution':
            raise ValidationError(
                {'created_by': 'Only partner institutions can create webinars.'}
            )

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or 'webinar'
            candidate = base_slug
            suffix = 1
            while Webinar.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate

        self.is_published = self.status == self.WebinarStatus.PUBLISHED
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
        if not self.is_published:
            self.published_at = None

        super().save(*args, **kwargs)

    # ── Editable statuses ────────────────────────────────────────────────────
    EDITABLE_STATUSES = frozenset(('draft', 'archived'))

    def is_editable(self):
        return self.status in self.EDITABLE_STATUSES

    # ── Status transition state machine ──────────────────────────────────────
    # Webinars publish in one step: the assigned host expert takes the webinar
    # straight from draft to published — no institution or admin approval gate.
    VALID_TRANSITIONS = {
        'draft': ('published',),
        'published': ('archived',),
        'archived': ('draft',),
    }

    REQUIRED_FOR_SUBMIT = ('title', 'description', 'scheduled_at', 'duration_minutes', 'meeting_url')

    def transition_to(self, new_status, actor=None):
        """
        Move the webinar to *new_status* with guard-rail checks.
        Raises ``ValidationError`` on illegal transitions or missing data.
        """
        allowed = self.VALID_TRANSITIONS.get(self.status, ())
        if new_status not in allowed:
            raise ValidationError(
                f'Cannot transition from "{self.status}" to "{new_status}". '
                f'Allowed: {", ".join(allowed) if allowed else "none (terminal state)"}.'
            )

        # ── Completeness check (publishing only) ──
        if new_status == 'published':
            self._validate_webinar_completeness()

        # ── Apply transition ──
        self.status = new_status
        self.save()

        logger.info(
            'Webinar %s (%s) transitioned to %s by %s',
            self.pk, self.slug, new_status,
            actor.email if actor else 'system',
        )

    def _validate_webinar_completeness(self):
        """Ensure the webinar is ready before it publishes. Collects all problems."""
        errors = {}

        for field_name in self.REQUIRED_FOR_SUBMIT:
            value = getattr(self, field_name, None)
            if not value or (isinstance(value, str) and not value.strip()):
                errors[field_name] = f'{field_name} is required before submitting.'

        if self.scheduled_at and self.scheduled_at <= timezone.now():
            errors['scheduled_at'] = 'Scheduled time must be in the future.'

        if self.host_expert_id is None:
            errors['host_expert'] = 'A host expert must be assigned before submitting.'

        if errors:
            raise ValidationError(errors)
