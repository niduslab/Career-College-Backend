from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.text import slugify
from datetime import date, timedelta

import random
import secrets
import logging

from auth.utils.upload_helpers import (
    institution_cover_path,
    institution_logo_path,
    instructor_photo_path,
    learner_photo_path,
)

logger = logging.getLogger(__name__)


# Create your models here.
class CustomUserManager(BaseUserManager):
    """
    Custom user manager where email is the unique identifier
    """
    
    def create_user(self, email, password=None, **extra_fields):
        """Create and return a regular user"""
        if not email:
            raise ValueError('The Email field must be set')
        
        email = self.normalize_email(email)
        user_type = extra_fields.get('user_type', 'customer')
        
        # Full name is required for every user type
        if not extra_fields.get('full_name'):
            raise ValueError('Full name is required for all users')
        
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        """Create and return a superuser"""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('is_email_verified', True)
        extra_fields.setdefault('user_type', 'admin')
        extra_fields.setdefault('full_name', 'Super Admin')
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        return self.create_user(email, password, **extra_fields)
    
    def get_queryset(self):
        """Return only non-deleted users by default"""
        return super().get_queryset().filter(is_deleted=False)
    
    def all_with_deleted(self):
        """Return all users including soft-deleted ones"""
        return super().get_queryset()
    
    def deleted_only(self):
        """Return only soft-deleted users"""
        return super().get_queryset().filter(is_deleted=True)
    

class User(AbstractUser):
    """
    Custom User Model with conditional required fields based on user_type
    """
    # Remove username field
    username = None
    
    # Core fields
    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=255, help_text="Required for all users")
    name_slug = models.SlugField(max_length=255, unique=True, blank=True, db_index=True)
    
    # User type choices
    USER_TYPE_CHOICES = (
        ('learner', 'Learner'),
        ('instructor', 'Instructor'),
        ('partner_institution', 'Partner Institution'),
        ('admin', 'Admin'),
    )
    user_type = models.CharField(
        max_length=20,
        choices=USER_TYPE_CHOICES,
        default='learner',
        db_index=True
    )
    
    # Use email as the username field
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    is_email_verified = models.BooleanField(
        default=False,
        help_text="Indicates if the user's email has been verified"
    )
    # Status fields
    is_active = models.BooleanField(
        default=True,
        help_text="User account is active (NOT restricted by admin)"
    )
    is_restricted_by_admin = models.BooleanField(
        default=False,
        help_text="Account restricted by admin - user CANNOT reactivate themselves"
    )
    is_verified = models.BooleanField(
        default=False,
        help_text="Verified account. Auto-set True for learners on registration; instructors and partner institutions require admin verification."
    )
    is_deleted = models.BooleanField(
        default=False,
        help_text="Indicates if the user has deleted their account (soft delete)"
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the user deleted their account"
    )
    deletion_reason = models.TextField(
        null=True,
        blank=True,
        help_text="Optional reason for account deletion"
    )
    # Timestamps
    registration_date = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # OTP fields with purpose tracking
    OTP_PURPOSE_CHOICES = (
        ('registration', 'Registration'),
        ('password_reset', 'Password Reset'),
    )
    otp_code = models.CharField(max_length=6, blank=True, null=True)
    otp_created_at = models.DateTimeField(blank=True, null=True)
    otp_purpose = models.CharField(max_length=20, choices=OTP_PURPOSE_CHOICES, blank=True, null=True)
    otp_verified = models.BooleanField(default=False)
    password_reset_token = models.CharField(max_length=64, blank=True, null=True, unique=True)
    password_reset_token_created_at = models.DateTimeField(blank=True, null=True)

    # Override reverse accessor clashes with built-in auth.User
    groups = models.ManyToManyField(
        'auth.Group',
        blank=True,
        related_name='accounts_user_set',
        help_text='The groups this user belongs to.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        blank=True,
        related_name='accounts_user_set',
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )

    # Use custom manager
    objects = CustomUserManager()
    
    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-registration_date']
        indexes = [
            # Login/auth lookups: email + is_deleted + is_active
            models.Index(fields=['email', 'is_deleted', 'is_active'], name='idx_user_email_status'),
            # Admin filtering: user_type + is_active
            models.Index(fields=['user_type', 'is_active'], name='idx_user_type_active'),
            # Soft-delete queries
            models.Index(fields=['is_deleted', 'deleted_at'], name='idx_user_soft_delete'),
            # OTP lookups: email + otp_purpose (verify/resend flows)
            models.Index(fields=['email', 'otp_purpose'], name='idx_user_email_otp'),
            # Email verification filtering
            models.Index(fields=['is_email_verified', 'user_type'], name='idx_user_verified_type'),
            # Admin restriction lookups
            models.Index(fields=['is_restricted_by_admin', 'is_active'], name='idx_user_restricted'),
        ]
    
    def __str__(self):
        return self.get_display_name()
    
    def get_display_name(self):
        """Get display name based on user type"""
        if self.user_type == 'admin':
            return self.full_name or 'Admin'
        return self.full_name or self.email
    
    def clean(self):
        """Validate fields based on user_type"""
        super().clean()

        if not self.full_name:
            raise ValidationError({'full_name': 'Full name is required for all users.'})
        
    
    def save(self, *args, **kwargs):
        """Override save with conditional validation"""
        
        skip_validation = kwargs.pop('skip_validation', False)
        
        if not skip_validation:
            try:
                self.clean()
            except ValidationError:
                # Allow save if validation fails (e.g., during social auth)
                pass

        # Keep slug in sync with full_name and ensure uniqueness.
        current_slug = None
        if self.pk:
            current_slug = User.objects.all_with_deleted().filter(pk=self.pk).values_list('name_slug', flat=True).first()

        needs_slug = not self.name_slug or current_slug is None or self.name_slug == current_slug
        if needs_slug:
            base_value = self.full_name or self.email.split('@')[0]
            base_slug = slugify(base_value) or 'user'
            candidate = base_slug
            suffix = 1
            while User.objects.all_with_deleted().filter(name_slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.name_slug = candidate
        
        super().save(*args, **kwargs)
    
    def generate_otp(self, purpose='registration'):
        """Generate a 6-digit OTP"""
        self.otp_code = str(random.randint(100000, 999999))
        self.otp_created_at = timezone.now()
        self.otp_purpose = purpose
        self.otp_verified = False
        try:
            self.save()
        except Exception as e:
            logger.error(f"Failed to save OTP for user {self.email}: {e}")
            raise
        return self.otp_code
    
    def verify_otp(self, otp, purpose='registration', clear_otp=True):
        """Verify OTP and check if it's still valid (2 minutes)"""
        if not self.otp_code or not self.otp_created_at:
            return False, "No OTP found. Please request a new one."
        
        if self.otp_purpose != purpose:
            return False, f"This OTP was generated for {self.otp_purpose}, not for {purpose}."
        
        expiry_time = self.otp_created_at + timedelta(minutes=2)
        if timezone.now() > expiry_time:
            return False, "OTP has expired. Please request a new one."
        
        if self.otp_code != otp:
            return False, "Invalid OTP. Please try again."
        
        if purpose == 'password_reset':
            self.otp_verified = True
            self.save()
        
        if clear_otp:
            self.otp_code = None
            self.otp_created_at = None
            self.otp_purpose = None
            self.otp_verified = False
            self.save()
        
        return True, "OTP verified successfully."
    def generate_password_reset_token(self):
        """Generate a secure token for password reset (valid for 15 minutes)"""
        self.password_reset_token = secrets.token_urlsafe(48)  # 64 char token
        self.password_reset_token_created_at = timezone.now()
        try:
            self.save(skip_validation=True)
        except Exception as e:
            logger.error(f"Failed to save password reset token for user {self.email}: {e}")
            raise
        return self.password_reset_token
    
    def verify_password_reset_token(self, token):
        """Verify password reset token and check if it's still valid (15 minutes)"""
        if not self.password_reset_token or not self.password_reset_token_created_at:
            return False, "No password reset token found. Please verify OTP first."
        
        if self.password_reset_token != token:
            return False, "Invalid or expired password reset token."
        
        # Check if token is expired (15 minutes)
        expiry_time = self.password_reset_token_created_at + timedelta(minutes=15)
        if timezone.now() > expiry_time:
            return False, "Password reset token has expired. Please verify OTP again."
        
        return True, "Token verified successfully."
    def clear_password_reset_data(self):
        """Clear all password reset related data"""
        self.otp_code = None
        self.otp_created_at = None
        self.otp_purpose = None
        self.otp_verified = False
        self.password_reset_token = None
        self.password_reset_token_created_at = None
        try:
            self.save(skip_validation=True)
        except Exception as e:
            logger.error(f"Failed to clear password reset data for user {self.email}: {e}")
            raise
    

    def update_password(self, new_password):
        """Update user password and clear reset data"""
        try:
            self.set_password(new_password)
            self.clear_password_reset_data()
        except Exception as e:
            logger.error(f"Failed to update password for user {self.email}: {e}")
            raise
        return True
    
    def soft_delete(self, reason=None):
        """
        Soft delete the user account
        Modify email to allow re-registration with same email
        """
        
        # Store original email before modification
        original_email = self.email
        
        # Modify email to: original@example.com -> original@example.com.deleted.USER_ID
        self.email = f"{original_email}.deleted.{self.id}"
        
        # Set deletion flags
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.deletion_reason = reason
        
        try:
            self.save(skip_validation=True)
        except Exception as e:
            # Rollback in-memory changes so the object stays consistent
            self.email = original_email
            self.is_deleted = False
            self.is_active = True
            self.deleted_at = None
            self.deletion_reason = None
            logger.error(f"Failed to soft delete user {original_email}: {e}")
            raise
        
        # Log the deletion
        logger.info(f"User soft deleted: {original_email} -> {self.email} (Reason: {reason or 'Not provided'})")


    @property
    def can_assign_admin(self):
        """Check if user can assign admin role"""
        return self.is_superuser

    @property
    def profile_photo_url(self):
        """Return the user's profile photo URL from their profile."""
        for attr in ('learner_profile', 'instructor_profile', 'partner_institution_profile'):
            profile = getattr(self, attr, None)
            if profile:
                photo = getattr(profile, 'profile_photo', None) or getattr(profile, 'logo', None)
                if photo:
                    return photo.url
        return None
    
    
class LearnerProfile(models.Model):
    """Coursera-style learner profile linked to User."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='learner_profile'
    )

    # ── Personal info ──
    profile_photo = models.ImageField(
        upload_to=learner_photo_path, blank=True, null=True
    )
    headline = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Short tagline shown under the name, e.g. "Data Analyst at Google"'
    )
    bio = models.TextField(blank=True, default='', help_text='About / bio section')
    date_of_birth = models.DateField(blank=True, null=True)


    # ── Location ──
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    country = models.CharField(max_length=100, blank=True, default='')

    # ── Professional / academic background ──
    EXPERIENCE_LEVEL_CHOICES = (
        ('student', 'Student / No experience'),
        ('entry', 'Entry level (0–2 years)'),
        ('mid', 'Mid level (3–5 years)'),
        ('senior', 'Senior level (6–10 years)'),
        ('expert', 'Expert (10+ years)'),
    )
    experience_level = models.CharField(
        max_length=10, choices=EXPERIENCE_LEVEL_CHOICES, blank=True, default=''
    )

    # ── Learning preferences ──
    learning_goal = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Primary learning objective, e.g. "Switch to a career in data science"'
    )
    interests = models.JSONField(
        default=list, blank=True,
        help_text='List of topic interests, e.g. ["Python", "Machine Learning"]'
    )
    preferred_language = models.CharField(max_length=50, blank=True, default='English')

    # ── Social links ──
    linkedin_url = models.URLField(blank=True, default='')
    github_url = models.URLField(blank=True, default='')
    website_url = models.URLField(blank=True, default='')

    # ── Privacy ──
    is_profile_public = models.BooleanField(
        default=True, help_text='Whether the profile is visible to other learners'
    )

    # ── Timestamps ──
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'learner_profiles'
        verbose_name = 'Learner Profile'
        verbose_name_plural = 'Learner Profiles'
        ordering = ['-created_at']
        indexes = [
            # Public profile listings filtered by country
            models.Index(fields=['is_profile_public', 'country'], name='idx_learner_public_country'),
            # Experience level filtering
            models.Index(fields=['experience_level'], name='idx_learner_exp_level'),
        ]

    def clean(self):
        super().clean()
        if self.user.user_type != 'learner':
            raise ValidationError('LearnerProfile can only be created for users with user_type "learner".')
        if self.date_of_birth and self.date_of_birth > date.today():
            raise ValidationError({'date_of_birth': 'Date of birth cannot be in the future.'})

    def __str__(self):
        return f"{self.user.full_name} — Learner Profile"

    @property
    def location(self):
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ', '.join(parts)


class Education(models.Model):
    """Individual education entry for a user (learner or instructor)."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='education_history'
    )

    DEGREE_CHOICES = (
        ('high_school', 'High School'),
        ('associate', 'Associate Degree'),
        ('bachelor', "Bachelor's Degree"),
        ('master', "Master's Degree"),
        ('doctorate', 'Doctorate'),
        ('diploma', 'Diploma'),
        ('certificate', 'Certificate'),
        ('other', 'Other'),
    )
    degree = models.CharField(max_length=20, choices=DEGREE_CHOICES)
    field_of_study = models.CharField(max_length=255, blank=True, default='')
    institution = models.CharField(max_length=255)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    is_current = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'education'
        verbose_name = 'Education'
        verbose_name_plural = 'Education'
        ordering = ['-is_current', '-end_date', '-start_date']
        indexes = [
            # User's education list (FK already indexed, add composite for ordering)
            models.Index(fields=['user', '-start_date'], name='idx_edu_user_start'),
            # Degree filtering across users
            models.Index(fields=['degree', 'institution'], name='idx_edu_degree_inst'),
        ]

    def clean(self):
        super().clean()
        if self.user.user_type not in ('learner', 'instructor'):
            raise ValidationError('Education entries are only allowed for learners and instructors.')
        if self.is_current and self.end_date:
            raise ValidationError({'end_date': 'Current education should not have an end date.'})
        if not self.is_current and not self.end_date:
            raise ValidationError({'end_date': 'End date is required for completed education.'})
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})

    def __str__(self):
        return f"{self.get_degree_display()} — {self.institution}"


class WorkExperience(models.Model):
    """Individual work experience entry for a user (learner or instructor)."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='work_history'
    )

    job_title = models.CharField(max_length=255)
    company = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True, default='')
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    is_current = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'work_experience'
        verbose_name = 'Work Experience'
        verbose_name_plural = 'Work Experiences'
        ordering = ['-is_current', '-end_date', '-start_date']
        indexes = [
            # User's work history list
            models.Index(fields=['user', '-start_date'], name='idx_work_user_start'),
            # Company search
            models.Index(fields=['company'], name='idx_work_company'),
        ]

    def clean(self):
        super().clean()
        if self.user.user_type not in ('learner', 'instructor'):
            raise ValidationError('Work experience entries are only allowed for learners and instructors.')
        if self.is_current and self.end_date:
            raise ValidationError({'end_date': 'Current position should not have an end date.'})
        if not self.is_current and not self.end_date:
            raise ValidationError({'end_date': 'End date is required for past positions.'})
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})

    def __str__(self):
        return f"{self.job_title} at {self.company}"


class InstructorProfile(models.Model):
    """Profile model for instructors."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='instructor_profile'
    )

    # ── Personal info ──
    profile_photo = models.ImageField(
        upload_to=instructor_photo_path, blank=True, null=True
    )
    headline = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Professional tagline, e.g. "Senior ML Engineer at Meta"'
    )
    bio = models.TextField(blank=True, default='', help_text='Instructor biography')

    # ── Location ──
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    country = models.CharField(max_length=100, blank=True, default='')

    # ── Professional details ──
    specialization = models.JSONField(
        default=list, blank=True,
        help_text='Areas of expertise, e.g. ["Deep Learning", "NLP"]'
    )
    years_of_experience = models.PositiveIntegerField(default=0)
    current_title = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Current job title'
    )
    current_organization = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Current employer or affiliation'
    )

    # ── Social links ──
    linkedin_url = models.URLField(blank=True, default='')
    github_url = models.URLField(blank=True, default='')
    website_url = models.URLField(blank=True, default='')

    # ── Verification & status ──
    is_verified = models.BooleanField(
        default=False, help_text='Admin-verified instructor'
    )
    is_accepting_students = models.BooleanField(default=True)

    # ── Institution affiliation ──
    affiliated_institution = models.ForeignKey(
        'PartnerInstitutionProfile',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='instructors',
        help_text='Partner institution this instructor belongs to (null if independent)'
    )

    ONBOARDING_SOURCE_CHOICES = (
        ('self', 'Self-Registered'),
        ('institution', 'Onboarded by Institution'),
    )
    onboarding_source = models.CharField(
        max_length=15, choices=ONBOARDING_SOURCE_CHOICES, default='self'
    )

    AFFILIATION_STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('removed', 'Removed'),
    )
    affiliation_status = models.CharField(
        max_length=10, choices=AFFILIATION_STATUS_CHOICES,
        blank=True, default='',
        help_text='Status of the institution affiliation'
    )
    affiliated_at = models.DateTimeField(
        blank=True, null=True,
        help_text='When the instructor joined the institution'
    )

    # ── Timestamps ──
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'instructor_profiles'
        verbose_name = 'Instructor Profile'
        verbose_name_plural = 'Instructor Profiles'
        ordering = ['-created_at']
        indexes = [
            # Verified instructor listings
            models.Index(fields=['is_verified', 'is_accepting_students'], name='idx_instr_verified_accept'),
            # Institution affiliation queries
            models.Index(fields=['affiliated_institution', 'affiliation_status'], name='idx_instr_affiliation'),
            # Location-based search
            models.Index(fields=['country', 'city'], name='idx_instr_location'),
        ]

    def clean(self):
        super().clean()
        if self.user.user_type != 'instructor':
            raise ValidationError('InstructorProfile can only be created for users with user_type "instructor".')
        if self.affiliated_institution and not self.affiliation_status:
            raise ValidationError({'affiliation_status': 'Affiliation status is required when linked to an institution.'})
        if not self.affiliated_institution:
            if self.affiliation_status:
                raise ValidationError({'affiliation_status': 'Affiliation status must be empty when not linked to any institution.'})
            if self.affiliated_at:
                raise ValidationError({'affiliated_at': 'Affiliated date must be empty when not linked to any institution.'})

    def __str__(self):
        return f"{self.user.full_name} — Instructor Profile"

    @property
    def location(self):
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ', '.join(parts)


class PartnerInstitutionProfile(models.Model):
    """Profile model for partner institutions."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='partner_institution_profile'
    )

    # ── Branding ──
    logo = models.ImageField(
        upload_to=institution_logo_path, blank=True, null=True
    )
    cover_image = models.ImageField(
        upload_to=institution_cover_path, blank=True, null=True
    )
    institution_name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True, db_index=True)
    tagline = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Short institution tagline'
    )
    description = models.TextField(blank=True, default='')

    # ── Institution details ──
    INSTITUTION_TYPE_CHOICES = (
        ('university', 'University'),
        ('college', 'College'),
        ('training_center', 'Training Center'),
        ('corporate', 'Corporate Training'),
        ('nonprofit', 'Non-Profit'),
        ('other', 'Other'),
    )
    institution_type = models.CharField(
        max_length=20, choices=INSTITUTION_TYPE_CHOICES, blank=True, default=''
    )
    founded_year = models.PositiveIntegerField(blank=True, null=True)

    # ── Location ──
    address = models.TextField(blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    country = models.CharField(max_length=100, blank=True, default='')

    # ── Contact ──
    contact_email = models.EmailField(blank=True, default='')
    contact_phone = models.CharField(max_length=20, blank=True, default='')
    website_url = models.URLField(blank=True, default='')
    linkedin_url = models.URLField(blank=True, default='')

    # ── Verification & status ──
    is_verified = models.BooleanField(
        default=False, help_text='Admin-verified partner institution'
    )
    is_active = models.BooleanField(default=True)

    # ── Timestamps ──
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'partner_institution_profiles'
        verbose_name = 'Partner Institution Profile'
        verbose_name_plural = 'Partner Institution Profiles'
        ordering = ['-created_at']
        indexes = [
            # Active verified institution listings
            models.Index(fields=['is_verified', 'is_active'], name='idx_partner_verified_active'),
            # Type + country filtering
            models.Index(fields=['institution_type', 'country'], name='idx_partner_type_country'),
            # Name search
            models.Index(fields=['institution_name'], name='idx_partner_name'),
        ]

    def clean(self):
        super().clean()
        if self.user.user_type != 'partner_institution':
            raise ValidationError('PartnerInstitutionProfile can only be created for users with user_type "partner_institution".')
        if not self.institution_name or not self.institution_name.strip():
            raise ValidationError({'institution_name': 'Institution name is required.'})
        if self.founded_year is not None:
            current_year = date.today().year
            if self.founded_year > current_year:
                raise ValidationError({'founded_year': 'Founded year cannot be in the future.'})
            if self.founded_year < 1800:
                raise ValidationError({'founded_year': 'Founded year seems unrealistic.'})

    def __str__(self):
        return self.institution_name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.institution_name) or 'institution'
            candidate = base_slug
            suffix = 1
            while PartnerInstitutionProfile.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base_slug}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    @property
    def location(self):
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ', '.join(parts)
