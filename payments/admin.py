from django.contrib import admin

from payments.all_models.order_models import Order


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('tran_id', 'user', 'course', 'webinar', 'amount', 'currency', 'status', 'paid_at', 'created_at')
    list_filter = ('status', 'currency')
    search_fields = ('tran_id', 'val_id', 'user__email', 'course__title', 'webinar__title')
    # Financial audit rows: everything read-only in admin; state changes flow
    # through the order service only.
    readonly_fields = (
        'user', 'course', 'webinar', 'amount', 'currency', 'tran_id', 'status',
        'val_id', 'gateway_payload', 'paid_at', 'created_at', 'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
