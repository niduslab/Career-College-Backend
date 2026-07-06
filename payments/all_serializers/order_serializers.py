from rest_framework import serializers

from payments.all_models.order_models import Order


class OrderSerializer(serializers.ModelSerializer):
    """Learner-facing order row. `gateway_payload` and `val_id` are never
    declared — raw gateway responses stay server-side (absence beats stripping).

    Exactly one of the course_*/webinar_* pairs is non-null, matching the
    order's purchase target; `item_type` says which.
    """

    item_type = serializers.CharField(read_only=True)
    course_title = serializers.SerializerMethodField()
    course_slug = serializers.SerializerMethodField()
    webinar_title = serializers.SerializerMethodField()
    webinar_slug = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'item_type', 'course_title', 'course_slug',
            'webinar_title', 'webinar_slug', 'amount', 'currency',
            'status', 'tran_id', 'paid_at', 'created_at',
        ]
        read_only_fields = fields

    def get_course_title(self, obj):
        return obj.course.title if obj.course_id else None

    def get_course_slug(self, obj):
        return obj.course.slug if obj.course_id else None

    def get_webinar_title(self, obj):
        return obj.webinar.title if obj.webinar_id else None

    def get_webinar_slug(self, obj):
        return obj.webinar.slug if obj.webinar_id else None
