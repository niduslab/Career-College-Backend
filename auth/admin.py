from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import (
    Education,
    InstructorProfile,
    LearnerProfile,
    PartnerInstitutionProfile,
    User,
    WorkExperience,
)


class EducationInline(admin.TabularInline):
    model = Education
    extra = 0
    fields = ('degree', 'institution', 'start_date', 'end_date', 'is_current')
    show_change_link = True


class WorkExperienceInline(admin.TabularInline):
    model = WorkExperience
    extra = 0
    fields = ('job_title', 'company', 'start_date', 'end_date', 'is_current')
    show_change_link = True


class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('email', 'full_name', 'user_type')


class CustomUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = '__all__'


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    ordering = ('-registration_date',)
    list_display = (
        'email',
        'full_name',
        'user_type',
        'is_active',
        'is_verified',
        'is_email_verified',
        'is_staff',
        'is_deleted',
        'registration_date',
    )
    list_filter = (
        'user_type',
        'is_active',
        'is_verified',
        'is_email_verified',
        'is_staff',
        'is_superuser',
        'is_deleted',
        'is_restricted_by_admin',
    )
    search_fields = ('email', 'full_name', 'name_slug')
    readonly_fields = ('registration_date', 'updated_at', 'deleted_at')
    inlines = (EducationInline, WorkExperienceInline)

    fieldsets = (
        ('Credentials', {'fields': ('email', 'password')}),
        (
            'Personal info',
            {
                'fields': (
                    'full_name',
                    'name_slug',
                    'user_type',
                    'is_email_verified',
                    'is_verified',
                )
            },
        ),
        (
            'OTP & Password Reset',
            {
                'fields': (
                    'otp_code',
                    'otp_created_at',
                    'otp_purpose',
                    'otp_verified',
                    'password_reset_token',
                    'password_reset_token_created_at',
                )
            },
        ),
        (
            'Permissions',
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                )
            },
        ),
        (
            'Account Status',
            {
                'fields': (
                    'is_restricted_by_admin',
                    'is_deleted',
                    'deleted_at',
                    'deletion_reason',
                )
            },
        ),
        ('Important dates', {'fields': ('last_login', 'registration_date', 'updated_at')}),
    )

    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': ('email', 'full_name', 'user_type', 'password1', 'password2'),
            },
        ),
    )


@admin.register(LearnerProfile)
class LearnerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'country', 'experience_level', 'is_profile_public', 'updated_at')
    list_filter = ('is_profile_public', 'experience_level', 'country')
    search_fields = ('user__email', 'user__full_name', 'headline', 'country', 'city')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(InstructorProfile)
class InstructorProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'is_verified',
        'is_accepting_students',
        'affiliated_institution',
        'affiliation_status',
        'updated_at',
    )
    list_filter = ('is_verified', 'is_accepting_students', 'affiliation_status', 'country')
    search_fields = ('user__email', 'user__full_name', 'headline', 'current_organization')
    readonly_fields = ('created_at', 'updated_at', 'affiliated_at')


@admin.register(PartnerInstitutionProfile)
class PartnerInstitutionProfileAdmin(admin.ModelAdmin):
    list_display = ('institution_name', 'institution_type', 'country', 'is_verified', 'is_active', 'updated_at')
    list_filter = ('institution_type', 'country', 'is_verified', 'is_active')
    search_fields = ('institution_name', 'slug', 'contact_email', 'user__email')
    readonly_fields = ('slug', 'created_at', 'updated_at')


@admin.register(Education)
class EducationAdmin(admin.ModelAdmin):
    list_display = ('user', 'degree', 'institution', 'start_date', 'end_date', 'is_current')
    list_filter = ('degree', 'is_current')
    search_fields = ('user__email', 'user__full_name', 'institution', 'field_of_study')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(WorkExperience)
class WorkExperienceAdmin(admin.ModelAdmin):
    list_display = ('user', 'job_title', 'company', 'start_date', 'end_date', 'is_current')
    list_filter = ('is_current', 'company')
    search_fields = ('user__email', 'user__full_name', 'job_title', 'company', 'location')
    readonly_fields = ('created_at', 'updated_at')
