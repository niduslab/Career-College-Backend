from django.contrib import admin

from webinars.models import Webinar, WebinarRegistration


@admin.register(Webinar)
class WebinarAdmin(admin.ModelAdmin):
    list_display = ('title', 'partner_institution', 'host_expert', 'status', 'scheduled_at', 'is_published')
    list_filter = ('status', 'is_published', 'meeting_provider')
    search_fields = ('title', 'slug', 'description')
    readonly_fields = ('slug', 'is_published', 'published_at', 'created_at', 'updated_at')
    raw_id_fields = ('partner_institution', 'host_expert', 'category', 'created_by', 'last_edited_by')


@admin.register(WebinarRegistration)
class WebinarRegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'webinar', 'is_active', 'attended', 'created_at')
    list_filter = ('is_active', 'attended')
    search_fields = ('user__email', 'webinar__title')
    raw_id_fields = ('user', 'webinar')
