import re

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.utils import timezone
from datetime import timedelta
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from auth.utils import validate_custom_password_strength

User = get_user_model()

# Generic email domains that are not allowed for partner institution registration
GENERIC_EMAIL_DOMAINS = {
    'gmail.com', 'yahoo.com', 'yahoo.co.in', 'hotmail.com', 'outlook.com',
    'live.com', 'aol.com', 'icloud.com', 'mail.com', 'zoho.com',
    'protonmail.com', 'proton.me', 'yandex.com', 'gmx.com', 'gmx.net',
    'rediffmail.com', 'tutanota.com', 'fastmail.com', 'msn.com',
    'qq.com', '163.com', 'mail.ru',
}


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

    INSTITUTION_TYPE_CHOICES = (
        'university', 'college', 'training_center', 'corporate', 'nonprofit', 'other',
    )

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
            profile.save(update_fields=['institution_name', 'institution_type'])

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

            # Prevent rapid OTP spam for password reset.
            if (
                user.otp_created_at
                and user.otp_purpose == 'password_reset'
                and timezone.now() - user.otp_created_at < timedelta(seconds=30)
            ):
                raise serializers.ValidationError({'email': 'Please wait before requesting another OTP.'})

            attrs['user'] = user
            return attrs
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to process forgot password request right now.'})

    def save(self, **kwargs):
        try:
            user = self.validated_data['user']
            user.generate_otp(purpose='password_reset')
            return user
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
            return user
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError({'detail': 'Unable to update password right now.'})
