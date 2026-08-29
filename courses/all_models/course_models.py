import logging
import os
import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from authentication.models import PartnerInstitutionProfile

logger = logging.getLogger(__name__)

def course_thumbnail_upload_path(instance, filename):
    """Generate deterministic, URL-safe upload path for course thumbnails."""
    base_name, ext = os.path.splitext(filename)
    slug = slugify(base_name) or 'thumbnail'
    unique_suffix = uuid.uuid4().hex[:10]
    return f"courses/thumbnails/{slug}_{unique_suffix}{ext.lower()}"


course_thumbnail_upload_path.__module__ = 'courses.models'


class TimestampedModel(models.Model):
    """Reusable timestamp fields for operational models."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuthoredModel(TimestampedModel):
    """Timestamps + authorship (`created_by` / `last_edited_by`) for expert-authored content.

    SET_NULL so removing an expert keeps their content; `related_name='+'` (reverse
    accessor unused). Powers partner-institution monitoring (SRS 7.2.1, 7.7.3).
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        abstract = True


class CourseCategory(models.Model):
    """Taxonomy for organizing courses into marketplace categories."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, db_index=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children',
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'course_categories'
        verbose_name = 'Course Category'
        verbose_name_plural = 'Course Categories'
        ordering = ['name']
        indexes = [
            models.Index(fields=['is_active', 'name'], name='idx_ccat_active_name'),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or 'category'
            candidate = base_slug
            suffix = 1
            while CourseCategory.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)
        if not self.is_active:
            # Deactivating a parent must not leave still-active children
            # invisible in the public tree (which only surfaces active
            # top-level categories and their active children).
            self.children.filter(is_active=True).update(is_active=False)

    def __str__(self):
        return self.name


class NidusCourse(models.Model):
    """Core course entity for a marketplace experience similar to Udemy."""

    class CourseStatus(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        INSTITUTION_REVIEW = 'institution_review', 'Institution Review'
        UNDER_REVIEW = 'under_review', 'Under Review'
        PUBLISHED = 'published', 'Published'
        REJECTED = 'rejected', 'Rejected'
        ARCHIVED = 'archived', 'Archived'

    class CourseLevel(models.TextChoices):
        BEGINNER = 'beginner', 'Beginner'
        INTERMEDIATE = 'intermediate', 'Intermediate'
        ADVANCED = 'advanced', 'Advanced'

    class DeliveryMode(models.TextChoices):
        SELF_PACED = 'self_paced', 'Self-Paced'
        SCHEDULED = 'scheduled', 'Scheduled (Cohort-Based)'

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_nidus_courses',
        help_text='User who initiated the course (instructor or partner institution).',
    )
    instructors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='instructed_nidus_courses',
        blank=True,
        help_text='One course can have one or many instructors.',
    )
    partner_institution = models.ForeignKey(
        PartnerInstitutionProfile,
        on_delete=models.SET_NULL,
        related_name='nidus_courses',
        blank=True,
        null=True,
        help_text='Partner institution that owns this course. Set automatically when a partner institution creates the course.',
    )
    category = models.ForeignKey(
        CourseCategory,
        on_delete=models.SET_NULL,
        related_name='courses',
        blank=True,
        null=True,
        db_index=True,
    )

    title = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=280, unique=True, db_index=True)
    description = models.TextField()
    learning_objectives = models.TextField(
        blank=True,
        default='',
        help_text='Outcome statements, one per line.',
    )
    prerequisites = models.TextField(
        blank=True,
        default='',
        help_text='Pre-course requirements, one per line.',
    )
    audiences = models.TextField(
        blank=True,
        default='',
        help_text='Intended audience segments, one per line.',
    )
    course_outline = models.TextField(
        blank=True,
        default='',
        help_text='Full topic/week-by-week outline. Required at submission for '
                   'scheduled (cohort) courses in place of fully-authored sections; '
                   'optional for self-paced courses.',
    )
    thumbnail = models.ImageField(upload_to=course_thumbnail_upload_path, blank=True, null=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text='Course list price in marketplace currency.',
    )
    language = models.CharField(max_length=50, default='English', db_index=True)
    level = models.CharField(
        max_length=20,
        choices=CourseLevel.choices,
        default=CourseLevel.BEGINNER,
        db_index=True,
    )
    duration_minutes = models.PositiveIntegerField(
        default=0,
        help_text='Total course video duration in minutes.',
    )
    learning_hours = models.PositiveIntegerField(
        default=0,
        help_text='Instructor-declared total learning hours, shown on the certificate. '
                  'Distinct from duration_minutes, which is raw video runtime.',
    )
    delivery_mode = models.CharField(
        max_length=20,
        choices=DeliveryMode.choices,
        default=DeliveryMode.SELF_PACED,
        db_index=True,
        help_text='Set at creation, immutable afterward. Scheduled courses do not '
                   'require sections at submission (a written course_outline stands '
                   'in) and require an attached CourseSchedule.',
    )
    status = models.CharField(
        max_length=20,
        choices=CourseStatus.choices,
        default=CourseStatus.DRAFT,
        db_index=True,
    )
    is_published = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Denormalized flag for fast published-course queries.',
    )
    rejection_reason = models.TextField(blank=True, default='')
    published_at = models.DateTimeField(blank=True, null=True)

    # Denormalized from CourseReview — updated by review_service._recalculate_course_avg
    # on every review create / update / delete. Enables O(1) catalog sort + filter.
    avg_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    review_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'nidus_courses'
        verbose_name = 'Nidus Course'
        verbose_name_plural = 'Nidus Courses'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at'], name='idx_ncourse_status_date'),
            models.Index(fields=['is_published', '-published_at'], name='idx_ncourse_pub_date'),
            models.Index(fields=['language', 'level'], name='idx_ncourse_lang_level'),
            models.Index(fields=['created_by', 'status'], name='idx_ncourse_creator_status'),
            # Institution analytics
            models.Index(fields=['partner_institution', 'status'], name='idx_ncourse_inst_status'),
            # Course Catalog
            models.Index(fields=['is_published', 'price'], name='idx_ncourse_pub_price'),
            models.Index(fields=['is_published', 'duration_minutes'], name='idx_ncourse_pub_duration'),
            # Catalog rating sort
            models.Index(fields=['is_published', 'avg_rating'], name='idx_ncourse_pub_rating'),
            models.Index(fields=['is_published', 'review_count'], name='idx_ncourse_pub_reviews'),
            # Catalog search 
            GinIndex(name='idx_ncourse_title_trgm', fields=['title'], opclasses=['gin_trgm_ops']),
            GinIndex(name='idx_ncourse_desc_trgm', fields=['description'], opclasses=['gin_trgm_ops']),
        ]

    def clean(self):
        super().clean()
        if self.created_by and self.created_by.user_type not in ('instructor', 'partner_institution'):
            raise ValidationError(
                {'created_by': 'Only instructors or partner institutions can create courses.'}
            )

        if self.status != self.CourseStatus.REJECTED and self.rejection_reason:
            raise ValidationError({'rejection_reason': 'Rejection reason is only valid for rejected courses.'})

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or 'course'
            candidate = base_slug
            suffix = 1
            while NidusCourse.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate

        self.is_published = self.status == self.CourseStatus.PUBLISHED
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
        if not self.is_published:
            self.published_at = None

        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.title} ({self.get_status_display()})'

    # ── Editable statuses ───────────────────────────────────────────────────
    EDITABLE_STATUSES = frozenset(('draft', 'rejected'))

    def is_editable(self):
        return self.status in self.EDITABLE_STATUSES

    # ── Status transition state machine ─────────────────────────────────────
    VALID_TRANSITIONS = {
        'draft': ('under_review', 'institution_review'),
        'institution_review': ('under_review', 'rejected'),
        'under_review': ('published', 'rejected'),
        'rejected': ('draft',),
        'published': ('archived',),
        'archived': ('draft',),
    }

    # Minimum requirements before an instructor can submit for review.
    REQUIRED_FOR_SUBMIT = ('title', 'description')

    def transition_to(self, new_status, reviewer=None, rejection_reason=''):
        """
        Move the course to *new_status* with guard-rail checks.
        Raises ``ValidationError`` on illegal transitions or missing data.
        """
        allowed = self.VALID_TRANSITIONS.get(self.status, ())
        if new_status not in allowed:
            raise ValidationError(
                f'Cannot transition from "{self.status}" to "{new_status}". '
                f'Allowed: {", ".join(allowed) if allowed else "none (terminal state)"}.'
            )

        # Ownership routing when leaving draft
        if self.status == 'draft':
            if self.partner_institution_id and new_status == 'under_review':
                raise ValidationError(
                    'Institution-owned courses go to institution review first. '
                    'The expert should use /finish/ instead of /submit/.'
                )
            if not self.partner_institution_id and new_status == 'institution_review':
                raise ValidationError(
                    'Only institution-owned courses use the institution-review stage.'
                )

        # Submission completeness check
        if self.status == 'draft' and new_status in ('under_review', 'institution_review'):
            self._validate_course_completeness()

        # Rejection requires a reason
        if new_status == 'rejected' and not rejection_reason.strip():
            raise ValidationError(
                {'rejection_reason': 'A reason is required when rejecting a course.'}
            )

        # Admin actions require a reviewer
        if new_status in ('published', 'rejected') and reviewer is None:
            raise ValidationError('A reviewer (admin) is required for this transition.')

        # Apply transition
        self.status = new_status

        if new_status == 'rejected':
            self.rejection_reason = rejection_reason.strip()
        else:
            self.rejection_reason = ''

        self.save()

        logger.info(
            'Course %s (%s) transitioned to %s by %s',
            self.pk, self.slug, new_status,
            reviewer.email if reviewer else 'instructor',
        )

    def content_outline_stats(self):
        """
        Section/content-authoring snapshot — pure read, never raises. Shared by
        submission validation and the admin review endpoint.
        """
        sections = list(self.sections.all())
        empty_titles = [s.title for s in sections if not s.contents.exists()]
        return {
            'total_sections': len(sections),
            'sections_with_content': len(sections) - len(empty_titles),
            'empty_section_titles': empty_titles,
        }

    def _validate_course_completeness(self):
        """
        Ensure the course meets minimum quality standards before submission.
        Collects all problems and raises a single ValidationError.
        """
        errors = {}

        # Required fields
        for field_name in self.REQUIRED_FOR_SUBMIT:
            value = getattr(self, field_name, None)
            if not value or (isinstance(value, str) and not value.strip()):
                errors[field_name] = f'{field_name} is required before submitting.'

        if self.delivery_mode == self.DeliveryMode.SELF_PACED:
            # Self-paced courses have no later "unlock" moment, so the full
            # curriculum must exist (and be filled in) up front.
            section_count = self.sections.count()
            if section_count == 0:
                errors['sections'] = 'Course must have at least one section.'
            else:
                stats = self.content_outline_stats()
                if stats['empty_section_titles']:
                    errors['empty_sections'] = (
                        f'These sections have no content: {", ".join(stats["empty_section_titles"])}.'
                    )
        else:
            # Scheduled (cohort) courses don't need sections at submission —
            # content can drip in over the run. A written outline stands in
            # for the curriculum so the admin can judge scope before approving.
            if not self.course_outline.strip():
                errors['course_outline'] = (
                    'A scheduled (cohort) course must have a course outline before '
                    'it can be submitted for review.'
                )

        #All video lectures must be done transcoding
        from courses.all_models.assessment_models import Quiz
        from courses.all_models.content_models import VideoAsset

        pending_videos = (
            VideoAsset.objects
            .filter(
                lecture__section__course=self,
                is_active=True,
            )
            .exclude(status=VideoAsset.Status.READY)
        )
        pending_count = pending_videos.count()
        if pending_count > 0:
            errors['video_processing'] = (
                f'{pending_count} video(s) are still processing or failed. '
                'All videos must be ready before submission.'
            )

        # Every quiz must have at least one question with a correct answer
        incomplete_quizzes = []
        for quiz in Quiz.objects.filter(section__course=self):
            questions = quiz.questions.all()
            if not questions.exists():
                incomplete_quizzes.append(f'"{quiz.title}" has no questions')
            else:
                for question in questions:
                    if not question.answers.filter(is_correct=True).exists():
                        incomplete_quizzes.append(
                            f'"{quiz.title}" - Q{question.position} has no correct answer'
                        )
        if incomplete_quizzes:
            errors['quizzes'] = f'Incomplete quizzes: {"; ".join(incomplete_quizzes)}.'

        # Every coding exercise must carry an evaluation script
        from courses.all_models.assessment_models import CodingExercise

        incomplete_exercises = [
            f'"{exercise.title}" is missing its evaluation script'
            for exercise in CodingExercise.objects
            .filter(section__course=self)
            .only('id', 'title', 'evaluation_script')
            if not (exercise.evaluation_script or '').strip()
        ]
        if incomplete_exercises:
            errors['coding_exercises'] = (
                f'Incomplete coding exercises: {"; ".join(incomplete_exercises)}.'
            )

        # Scheduled (cohort) courses must have an attached schedule with sane dates.
        if self.delivery_mode == self.DeliveryMode.SCHEDULED:
            schedules = list(self.schedules.all())
            if not schedules:
                errors['schedules'] = (
                    'A scheduled (cohort) course must have at least one schedule attached '
                    'before it can be submitted for review.'
                )
            else:
                bad = []
                for sch in schedules:
                    problems = sch.date_logic_errors()
                    if problems:
                        label = sch.cohort_label or f'Schedule {sch.pk}'
                        bad.append(f'{label}: ' + '; '.join(problems.values()))
                if bad:
                    errors['schedule_dates'] = ' | '.join(bad)

        if errors:
            raise ValidationError(errors)


class CourseSection(AuthoredModel):
    """Logical grouping of course content (e.g., Introduction, Advanced Topics)."""

    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='sections',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    position = models.PositiveIntegerField(default=1, db_index=True)
    unlocks_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text='Drip-release time for scheduled cohorts. Null = unlocked immediately.',
    )

    class Meta:
        db_table = 'course_sections'
        verbose_name = 'Course Section'
        verbose_name_plural = 'Course Sections'
        ordering = ['course_id', 'position', 'id']
        constraints = [
            models.UniqueConstraint(fields=['course', 'position'], name='uniq_csection_course_position'),
        ]
        indexes = [
            models.Index(fields=['course', 'position'], name='idx_csection_course_position'),
        ]

    def __str__(self):
        return f'{self.course.title} - {self.title}'


class CourseInstructorInvite(TimestampedModel):
    """Tracks an owner's invitation to a verified instructor to co-author a course."""

    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_DECLINED = 'declined'
    STATUS_EXPIRED = 'expired'
    STATUS_REVOKED = 'revoked'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_REVOKED, 'Revoked'),
    ]

    course = models.ForeignKey(
        NidusCourse,
        on_delete=models.CASCADE,
        related_name='instructor_invites',
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_instructor_invites',
    )
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='received_instructor_invites',
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'course_instructor_invites'
        verbose_name = 'Course Instructor Invite'
        verbose_name_plural = 'Course Instructor Invites'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'invited_user'],
                condition=models.Q(status='pending'),
                name='unique_pending_invite_per_course',
            )
        ]
        indexes = [
            models.Index(fields=['course', 'status'], name='idx_invite_course_status'),
            models.Index(fields=['invited_user', 'status'], name='idx_invite_user_status'),
        ]

    def __str__(self):
        return f'Invite: {self.invited_user.email} → {self.course.title} [{self.status}]'
