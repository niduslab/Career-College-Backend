from django.contrib import admin

from id_verification.models import IdentityVerification, InstitutionVerification


@admin.register(IdentityVerification)
class IdentityVerificationAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'user',
        'document_type',
        'issuing_country',
        'status',
        'created_at',
        'submitted_at',
        'reviewed_by',
        'reviewed_at',
    )
    list_filter = ('status', 'document_type', 'issuing_country')
    search_fields = ('user__email', 'user__full_name', 'document_number')
    readonly_fields = ('created_at', 'submitted_at', 'updated_at')
    raw_id_fields = ('user', 'reviewed_by')
    ordering = ('-submitted_at',)


@admin.register(InstitutionVerification)
class InstitutionVerificationAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'institution',
        'registration_number',
        'issuing_authority',
        'status',
        'created_at',
        'submitted_at',
        'reviewed_by',
        'reviewed_at',
    )
    list_filter = ('status', 'issuing_authority')
    search_fields = (
        'institution__institution_name',
        'institution__user__email',
        'registration_number',
    )
    readonly_fields = ('created_at', 'submitted_at', 'updated_at')
    raw_id_fields = ('institution', 'reviewed_by')
    ordering = ('-submitted_at',)
