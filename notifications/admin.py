from django.contrib import admin

from .models import Notification, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'recipient', 'event_type', 'is_read', 'created_at')
    list_filter = ('event_type', 'is_read')
    search_fields = ('recipient__email', 'title')
    readonly_fields = ('created_at', 'read_at')


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'category', 'email_enabled', 'push_enabled')
    list_filter = ('category', 'email_enabled', 'push_enabled')
    search_fields = ('user__email',)
