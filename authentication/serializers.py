import re

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.utils import timezone
from datetime import timedelta
from rest_framework import serializers
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.utils import validate_custom_password_strength
from authentication.models import (
    Department,
    Education,
    InstructorProfile,
    LearnerProfile,
    PartnerInstitutionProfile,
    WorkExperience,
)

User = get_user_model()


def _blacklist_all_tokens(user):
    """Blacklist every outstanding refresh token for the user.

    Called after password change/reset so stolen refresh tokens cannot
    be used to issue new access tokens after the password is updated.
    Errors are swallowed — a blacklist failure must not roll back a
    successful password change.
    """
    try:
        for token in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token)
    except Exception:
        pass


# Generic email domains that are not allowed for partner institution registration
GENERIC_EMAIL_DOMAINS = {
    'gmail.com', 'yahoo.com', 'yahoo.co.in', 'hotmail.com', 'outlook.com',
    'live.com', 'aol.com', 'icloud.com', 'mail.com', 'zoho.com',
    'protonmail.com', 'proton.me', 'yandex.com', 'gmx.com', 'gmx.net',
    'rediffmail.com', 'tutanota.com', 'fastmail.com', 'msn.com',
    'qq.com', '163.com', 'mail.ru',
}

INSTITUTION_TYPE_CHOICES = (
    'university', 'college', 'training_center', 'corporate', 'nonprofit', 'other',
)


class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        return value.lower().strip()

    def validate(self, attrs):
        email = attrs['email']
        password = attrs['password']
        generic_error = 'Invalid email or password.'

        # Look up user including soft-deleted to give consistent timing
        user = User.objects.all_with_deleted().filter(email__iexact=email).first()

        if not user or user.is_deleted:
            raise serializers.ValidationError(generic_error)

        if not user.is_active or user.is_restricted_by_admin:
            raise serializers.ValidationError(
                'Your account has been deactivated or restricted. Please contact support.'
            )

        if not user.is_email_verified:
            raise serializers.ValidationError(
                'Please verify your email before logging in.'
            )

        user = authenticate(email=email, password=password)
        if user is None:
            raise serializers.ValidationError(generic_error)

        attrs['user'] = user
        return attrs

    def get_tokens(self, user):
        refresh = RefreshToken.for_user(user)
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate_refresh(self, value):
        try:
            self.token = RefreshToken(value)
        except Exception:
            raise serializers.ValidationError('Invalid or expired refresh token.')
        return value

    def save(self):
        self.token.blacklist()


class UserRegistrationSerializer(serializers.ModelSerializer):
    ALLOWED_REGISTRATION_TYPES = ('learner', 'instructor', 'partner_institution')
    INSTITUTION_TYPE_CHOICES = INSTITUTION_TYPE_CHOICES

    password = serializers.CharField(
        write_only=True, min_length=8, validators=[validate_password, validate_custom_password_strength]
    )
    confirm_password = serializers.CharField(write_only=True, min_length=8)
    user_type = serializers.ChoiceField(
        choices=ALLOWED_REGISTRATION_TYPES,
        help_text='Required. Register as learner, instructor, or partner_institution.',
    )
    # Required only for partner_institution
    institution_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default='',
        help_text='Required for partner institution registration.',
    )
    institution_type = serializers.ChoiceField(
        choices=INSTITUTION_TYPE_CHOICES,
        required=False, default='',
        help_text='Required for partner institution registration. Options: university, college, training_center, corporate, nonprofit, other.',
    )

    class Meta:
        model = User
        fields = [
            'email', 'full_name', 'password', 'confirm_password',
            'user_type', 'institution_name', 'institution_type',
        ]
        extra_kwargs = {
            'email': {'required': True},
            'full_name': {'required': True},
        }

    def validate_email(self, value):
        email = value.lower().strip()
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return email

    def validate_full_name(self, value):
        name = value.strip()
        if len(name) < 2:
            raise serializers.ValidationError('Full name must be at least 2 characters.')
        return name

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError(
                {'confirm_password': 'Passwords do not match.'}
            )

        user_type = attrs.get('user_type')
        email = attrs.get('email', '')
        institution_name = attrs.get('institution_name', '').strip()
        institution_type = attrs.get('institution_type', '')

        if user_type == 'partner_institution':
            # Institution name is mandatory
            if not institution_name:
                raise serializers.ValidationError(
                    {'institution_name': 'Institution name is required for partner institution registration.'}
                )

            # Institution type is mandatory
            if not institution_type:
                raise serializers.ValidationError(
                    {'institution_type': 'Institution type is required for partner institution registration.'}
                )

            # Must use institutional email (not generic)
            domain = email.rsplit('@', 1)[-1].lower() if '@' in email else ''
            if domain in GENERIC_EMAIL_DOMAINS:
                raise serializers.ValidationError(
                    {'email': 'Partner institutions must register with an official institutional email address, not a personal email.'}
                )

        return attrs

    def create(self, validated_data):
        validated_data.pop('confirm_password')
        password = validated_data.pop('password')
        institution_name = validated_data.pop('institution_name', '').strip()
        institution_type = validated_data.pop('institution_type', '')
        user_type = validated_data.get('user_type')

        # Learners are auto-verified; instructors and partner institutions are not
        validated_data['is_verified'] = (user_type == 'learner')

        try:
            user = User.objects.create_user(password=password, **validated_data)
        except IntegrityError:
            raise serializers.ValidationError(
                {'email': 'A user with this email already exists.'}
            )

        # Profile is auto-created by post_save signal.
        # Update partner institution profile with registration-time data.
        if user_type == 'partner_institution':
            profile = user.partner_institution_profile
            profile.institution_name = institution_name
            profile.institution_type = institution_type
            # slug is generated in save() from institution_name (NULL until now);
            # include it in update_fields so the generated value persists.
            profile.save(update_fields=['institution_name', 'institution_type', 'slug'])

        user.generate_otp(purpose='registration')
        return user



class VerifyOTPSerializer(serializers.Serializer):
    """Serializer for OTP verification"""
    email = serializers.EmailField(
        help_text='Email address associated with the account'
    )
    otp = serializers.CharField(
        max_length=6,
        min_length=6,
        help_text='6-digit OTP code sent to your email'
    )
    purpose = serializers.ChoiceField(
        choices=['registration', 'password_reset'],
        default='registration',
        help_text='Purpose of OTP verification'
    )

    def validate_email(self, value):
        """Validate and normalize email"""
        try:
            return value.lower().strip()
        except Exception:
            raise serializers.ValidationError('Unable to validate email right now.')

    def validate_otp(self, value):
        """Validate OTP format"""
        try:
            otp = re.sub(r'[\s\-]', '', value)

            if not otp.isdigit():
                raise serializers.ValidationError('OTP must contain only digits.')

            if len(otp) != 6:
                raise serializers.ValidationError('OTP must be exactly 6 digits.')

            return otp
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError('Unable to validate OTP right now.')

    def validate(self, attrs):
        try:
            email = attrs['email'].strip().lower()
            purpose = attrs['purpose']

            user = User.objects.all_with_deleted().filter(email__iexact=email).first()
            if not user:
                raise serializers.ValidationError({'email': 'No account found with this email.'})

            if user.is_deleted:
                raise serializers.ValidationError({'email': 'This account has been deleted.'})
            if not user.is_active:
                raise serializers.ValidationError({'email': 'This account is inactive.'})
            if user.is_restricted_by_admin:
                raise serializers.ValidationError({'email': 'This account is restricted by admin.'})

            if purpose == 'registration' and user.is_email_verified:
                raise serializers.ValidationError({'purpose': 'Email is already verified.'})

            if purpose == 'password_reset' and not user.is_email_verified:
                raise serializers.ValidationError({'purpose': 'Email must be verified before password reset.'})

            attrs['email'] = email
            attrs['user'] = user
            return attrs
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to validate OTP request right now.'})

    def save(self, **kwargs):
        try:
            user = self.validated_data['user']
            otp = self.validated_data['otp']
            purpose = self.validated_data['purpose']

            clear_otp = purpose == 'registration'
            is_valid, message = user.verify_otp(otp=otp, purpose=purpose, clear_otp=clear_otp)
            if not is_valid:
                raise serializers.ValidationError({'otp': message})

            response = {'message': message, 'purpose': purpose}

            if purpose == 'registration':
                user.is_email_verified = True
                user.save(update_fields=['is_email_verified', 'updated_at'])
                response['is_email_verified'] = True

            if purpose == 'password_reset':
                # Clear OTP code while keeping otp_verified=True for reset-token workflow.
                user.otp_code = None
                user.otp_created_at = None
                user.otp_purpose = None
                user.save(skip_validation=True)
                response['password_reset_token'] = user.generate_password_reset_token()

            return response
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to verify OTP right now.'})


class ResendOTPSerializer(serializers.Serializer):
    """Serializer for resending OTP"""
    email = serializers.EmailField(
        help_text='Email address to resend OTP to'
    )
    purpose = serializers.ChoiceField(
        choices=['registration', 'password_reset'],
        default='registration',
        help_text='Purpose of OTP'
    )

    def validate_email(self, value):
        """Validate and normalize email"""
        try:
            return value.lower().strip()
        except Exception:
            raise serializers.ValidationError('Unable to validate email right now.')

    def validate(self, attrs):
        try:
            email = attrs['email'].strip().lower()
            purpose = attrs['purpose']

            user = User.objects.all_with_deleted().filter(email__iexact=email).first()
            if not user:
                raise serializers.ValidationError({'email': 'No account found with this email.'})

            if user.is_deleted:
                raise serializers.ValidationError({'email': 'This account has been deleted.'})
            if not user.is_active:
                raise serializers.ValidationError({'email': 'This account is inactive.'})
            if user.is_restricted_by_admin:
                raise serializers.ValidationError({'email': 'This account is restricted by admin.'})

            if purpose == 'registration' and user.is_email_verified:
                raise serializers.ValidationError({'purpose': 'Email is already verified.'})

            if purpose == 'password_reset' and not user.is_email_verified:
                raise serializers.ValidationError({'purpose': 'Email must be verified before password reset.'})

            # Prevent rapid OTP spam for the same purpose.
            if (
                user.otp_created_at
                and user.otp_purpose == purpose
                and timezone.now() - user.otp_created_at < timedelta(seconds=30)
            ):
                raise serializers.ValidationError({'purpose': 'Please wait before requesting another OTP.'})

            attrs['email'] = email
            attrs['user'] = user
            return attrs
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to process OTP resend right now.'})

    def save(self, **kwargs):
        try:
            user = self.validated_data['user']
            purpose = self.validated_data['purpose']
            user.generate_otp(purpose=purpose)
            return {'message': 'OTP sent successfully.', 'purpose': purpose}
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to resend OTP right now.'})


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(
        help_text='Email address associated with the account'
    )

    def validate_email(self, value):
        try:
            return value.lower().strip()
        except Exception:
            raise serializers.ValidationError('Unable to validate email right now.')

    def validate(self, attrs):
        try:
            email = attrs['email']
            user = User.objects.all_with_deleted().filter(email__iexact=email).first()

            # Silently discard ineligible accounts — never reveal existence or state
            # to the caller. attrs['user'] is absent when no OTP should be sent;
            # save() treats that as a no-op and the view always returns the same 200.
            eligible = (
                user is not None
                and not user.is_deleted
                and user.is_active
                and not user.is_restricted_by_admin
                and user.is_email_verified
                and not (
                    user.otp_created_at
                    and user.otp_purpose == 'password_reset'
                    and timezone.now() - user.otp_created_at < timedelta(seconds=30)
                )
            )
            if eligible:
                attrs['user'] = user
            return attrs
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to process forgot password request right now.'})

    def save(self, **kwargs):
        try:
            user = self.validated_data.get('user')
            if user:
                user.generate_otp(purpose='password_reset')
            return user  # None when ineligible; caller must handle
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to generate password reset OTP right now.'})


class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(
        help_text='Email address associated with the account'
    )
    reset_token = serializers.CharField(
        max_length=128,
        help_text='Password reset token returned after OTP verification'
    )
    new_password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    confirm_password = serializers.CharField(write_only=True, min_length=8, max_length=128)

    def validate_new_password(self, value):
        """Validate password strength"""
        validate_password(value)
        validate_custom_password_strength(value)
        return value

    def validate_email(self, value):
        try:
            return value.lower().strip()
        except Exception:
            raise serializers.ValidationError('Unable to validate email right now.')

    def validate(self, attrs):
        try:
            email = attrs['email']
            reset_token = attrs['reset_token']
            new_password = attrs['new_password']
            confirm_password = attrs['confirm_password']

            if new_password != confirm_password:
                raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})

            user = User.objects.all_with_deleted().filter(email__iexact=email).first()
            if not user:
                raise serializers.ValidationError({'email': 'No account found with this email.'})
            if user.is_deleted:
                raise serializers.ValidationError({'email': 'This account has been deleted.'})
            if not user.is_active:
                raise serializers.ValidationError({'email': 'This account is inactive.'})
            if user.is_restricted_by_admin:
                raise serializers.ValidationError({'email': 'This account is restricted by admin.'})
            if not user.is_email_verified:
                raise serializers.ValidationError({'email': 'Email must be verified before password reset.'})

            token_ok, token_message = user.verify_password_reset_token(reset_token)
            if not token_ok:
                raise serializers.ValidationError({'reset_token': token_message})

            validate_password(new_password, user=user)
            attrs['user'] = user
            return attrs
        except serializers.ValidationError:
            raise
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'new_password': list(exc.messages)})
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to validate reset password request right now.'})

    def save(self, **kwargs):
        try:
            user = self.validated_data['user']
            new_password = self.validated_data['new_password']
            user.update_password(new_password)
            _blacklist_all_tokens(user)
            return user
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to reset password right now.'})


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    new_password = serializers.CharField(write_only=True, min_length=8, max_length=128)
    confirm_password = serializers.CharField(write_only=True, min_length=8, max_length=128)

    def validate_new_password(self, value):
        """Validate password strength"""
        validate_password(value)
        validate_custom_password_strength(value)
        return value

    def validate(self, attrs):
        try:
            request = self.context.get('request')
            user = getattr(request, 'user', None)
            if not user or not user.is_authenticated:
                raise serializers.ValidationError({'detail': 'Authentication is required.'})

            current_password = attrs['current_password']
            new_password = attrs['new_password']
            confirm_password = attrs['confirm_password']

            if not user.check_password(current_password):
                raise serializers.ValidationError({'current_password': 'Current password is incorrect.'})

            if new_password != confirm_password:
                raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})

            if current_password == new_password:
                raise serializers.ValidationError({'new_password': 'New password must be different from current password.'})

            validate_password(new_password, user=user)
            attrs['user'] = user
            return attrs
        except serializers.ValidationError:
            raise
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'new_password': list(exc.messages)})
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to validate password change right now.'})

    def save(self, **kwargs):
        try:
            user = self.validated_data['user']
            new_password = self.validated_data['new_password']
            user.set_password(new_password)
            user.save(update_fields=['password', 'updated_at'])
            _blacklist_all_tokens(user)
            return user
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to update password right now.'})

class EducationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Education
        fields = [
            'id', 'degree', 'field_of_study', 'institution',
            'start_date', 'end_date', 'is_current',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        is_current = attrs.get('is_current', getattr(self.instance, 'is_current', False))
        end_date = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        start_date = attrs.get('start_date', getattr(self.instance, 'start_date', None))

        if is_current and end_date:
            raise serializers.ValidationError({'end_date': 'Current education should not have an end date.'})
        if not is_current and not end_date:
            raise serializers.ValidationError({'end_date': 'End date is required for completed education.'})
        if end_date and start_date and end_date < start_date:
            raise serializers.ValidationError({'end_date': 'End date cannot be before start date.'})
        return attrs


class WorkExperienceSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkExperience
        fields = [
            'id', 'job_title', 'company', 'location',
            'start_date', 'end_date', 'is_current',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        is_current = attrs.get('is_current', getattr(self.instance, 'is_current', False))
        end_date = attrs.get('end_date', getattr(self.instance, 'end_date', None))
        start_date = attrs.get('start_date', getattr(self.instance, 'start_date', None))

        if is_current and end_date:
            raise serializers.ValidationError({'end_date': 'Current position should not have an end date.'})
        if not is_current and not end_date:
            raise serializers.ValidationError({'end_date': 'End date is required for past positions.'})
        if end_date and start_date and end_date < start_date:
            raise serializers.ValidationError({'end_date': 'End date cannot be before start date.'})
        return attrs


# ── Owner (private) profile serializers ──────────────────────

class LearnerProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearnerProfile
        exclude = ['user']
        read_only_fields = ['id', 'created_at', 'updated_at']


class InstructorProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstructorProfile
        exclude = ['user']
        read_only_fields = [
            'id', 'created_at', 'updated_at',
            'is_verified', 'affiliated_institution',
            'affiliation_status', 'affiliated_at', 'onboarding_source',
        ]


class PartnerInstitutionProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerInstitutionProfile
        exclude = ['user']
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at', 'is_verified']


class UserBasicSerializer(serializers.ModelSerializer):
    """Minimal user info included in profile responses."""
    class Meta:
        model = User
        fields = [
            'id', 'email', 'full_name', 'name_slug', 'user_type',
            'is_email_verified', 'is_verified', 'registration_date',
        ]
        read_only_fields = fields


# ── Public profile serializers (read-only, limited fields) ───

class PublicEducationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Education
        fields = ['degree', 'field_of_study', 'institution', 'start_date', 'end_date', 'is_current']


class PublicWorkExperienceSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkExperience
        fields = ['job_title', 'company', 'location', 'start_date', 'end_date', 'is_current']


class PublicLearnerProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='user.full_name', read_only=True)
    slug = serializers.CharField(source='user.name_slug', read_only=True)
    education = serializers.SerializerMethodField()
    work_experience = serializers.SerializerMethodField()

    class Meta:
        model = LearnerProfile
        fields = [
            'full_name', 'slug', 'profile_photo', 'headline', 'bio',
            'city', 'state', 'country', 'experience_level',
            'learning_goal', 'interests', 'preferred_language',
            'linkedin_url', 'github_url', 'website_url',
            'education', 'work_experience',
        ]

    def get_education(self, obj):
        return PublicEducationSerializer(obj.user.education_history.all(), many=True).data

    def get_work_experience(self, obj):
        return PublicWorkExperienceSerializer(obj.user.work_history.all(), many=True).data


class PublicInstructorProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='user.full_name', read_only=True)
    slug = serializers.CharField(source='user.name_slug', read_only=True)
    education = serializers.SerializerMethodField()
    work_experience = serializers.SerializerMethodField()

    class Meta:
        model = InstructorProfile
        fields = [
            'full_name', 'slug', 'profile_photo', 'headline', 'bio',
            'city', 'state', 'country',
            'specialization', 'years_of_experience',
            'current_title', 'current_organization',
            'linkedin_url', 'github_url', 'website_url',
            'is_verified', 'is_accepting_students',
            'education', 'work_experience',
        ]

    def get_education(self, obj):
        return PublicEducationSerializer(obj.user.education_history.all(), many=True).data

    def get_work_experience(self, obj):
        return PublicWorkExperienceSerializer(obj.user.work_history.all(), many=True).data


class PublicPartnerInstitutionProfileSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(read_only=True)

    class Meta:
        model = PartnerInstitutionProfile
        fields = [
            'institution_name', 'slug', 'logo', 'cover_image',
            'tagline', 'description', 'institution_type', 'founded_year',
            'city', 'state', 'country',
            'contact_email', 'website_url', 'linkedin_url',
            'is_verified',
        ]


# ── List serializers (compact, for browse pages) ─────────────

class LearnerListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='user.full_name', read_only=True)
    slug = serializers.CharField(source='user.name_slug', read_only=True)

    class Meta:
        model = LearnerProfile
        fields = [
            'full_name', 'slug', 'profile_photo', 'headline',
            'country', 'experience_level',
        ]


class InstructorListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='user.full_name', read_only=True)
    slug = serializers.CharField(source='user.name_slug', read_only=True)

    class Meta:
        model = InstructorProfile
        fields = [
            'full_name', 'slug', 'profile_photo', 'headline',
            'country', 'specialization', 'is_verified',
        ]


class InstitutionListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerInstitutionProfile
        fields = [
            'institution_name', 'slug', 'logo', 'tagline',
            'institution_type', 'country', 'is_verified',
        ]


# ── Expert management (partner institution) ──────────────────

class ExpertCreateSerializer(serializers.Serializer):
    """Input for an institution onboarding a new expert (instructor)."""

    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    bio = serializers.CharField(required=False, allow_blank=True, default='')
    headline = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default='',
    )
    department_id = serializers.IntegerField(required=False, allow_null=True)
    specialization = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False, default=list,
    )

    def validate_full_name(self, value):
        name = value.strip()
        if len(name) < 2:
            raise serializers.ValidationError('Full name must be at least 2 characters.')
        return name


class ExpertUpdateSerializer(serializers.Serializer):
    """Input for editing an affiliated expert's profile + activation state."""

    bio = serializers.CharField(required=False, allow_blank=True)
    headline = serializers.CharField(max_length=255, required=False, allow_blank=True)
    department_id = serializers.IntegerField(required=False, allow_null=True)
    specialization = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False,
    )
    is_active = serializers.BooleanField(required=False)


class DepartmentSerializer(serializers.ModelSerializer):
    """Read/write representation of an institution department. Input is just `name`."""

    class Meta:
        model = Department
        fields = ['id', 'name', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'is_active', 'created_at', 'updated_at']


class ExpertListSerializer(serializers.ModelSerializer):
    """Read representation of an institution's expert."""

    id = serializers.IntegerField(read_only=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    full_name = serializers.CharField(source='user.full_name', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    slug = serializers.CharField(source='user.name_slug', read_only=True)
    is_email_verified = serializers.BooleanField(
        source='user.is_email_verified', read_only=True,
    )
    department = DepartmentSerializer(read_only=True)
    course_count = serializers.SerializerMethodField()

    class Meta:
        model = InstructorProfile
        fields = [
            'id', 'user_id', 'full_name', 'email', 'slug', 'profile_photo',
            'headline', 'bio', 'department', 'specialization',
            'is_verified', 'is_email_verified',
            'affiliation_status', 'onboarding_source', 'affiliated_at',
            'course_count',
        ]

    def get_course_count(self, obj):
        # Prefer the annotation from institution_experts_qs (avoids N+1 on the
        # list endpoint); fall back to a direct count for un-annotated instances
        # (e.g. the single object returned by the create endpoint).
        annotated = getattr(obj, '_course_count', None)
        if annotated is not None:
            return annotated
        return obj.user.instructed_nidus_courses.count()
