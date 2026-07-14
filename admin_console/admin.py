from django.contrib import admin

from admin_console.all_models import AdminSession


@admin.register(AdminSession)
class AdminSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'browser', 'os', 'device', 'ip_address', 'last_seen_at')
    list_filter = ('browser', 'os')
    search_fields = ('user__email', 'ip_address', 'session_key')
    readonly_fields = (
        'user', 'session_key', 'ip_address', 'user_agent',
        'browser', 'os', 'device', 'created_at', 'last_seen_at',
    )
