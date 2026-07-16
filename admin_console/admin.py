from django.contrib import admin

from admin_console.all_models import AdminActionLog, AdminSession


@admin.register(AdminSession)
class AdminSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'browser', 'os', 'device', 'ip_address', 'last_seen_at')
    list_filter = ('browser', 'os')
    search_fields = ('user__email', 'ip_address', 'session_key')
    readonly_fields = (
        'user', 'session_key', 'ip_address', 'user_agent',
        'browser', 'os', 'device', 'created_at', 'last_seen_at',
    )


@admin.register(AdminActionLog)
class AdminActionLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'target_user', 'actor', 'created_at')
    list_filter = ('action',)
    search_fields = ('target_user__email', 'actor__email', 'reason')
    readonly_fields = ('actor', 'target_user', 'action', 'reason', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False  # append-only; written by the service

    def has_change_permission(self, request, obj=None):
        return False
