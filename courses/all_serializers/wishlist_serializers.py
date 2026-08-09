from rest_framework import serializers

from courses.all_models.wishlist_models import Wishlist
from courses.all_serializers.enrollment_serializers import CatalogCourseListSerializer


class WishlistItemSerializer(serializers.ModelSerializer):
    """A wishlist row with the nested catalog card.

    Shape mirrors EnrollmentSerializer so the frontend can render the wishlist
    grid with the same card component it uses for the catalog and my-courses.
    """

    course = CatalogCourseListSerializer(read_only=True)

    class Meta:
        model = Wishlist
        fields = ['id', 'course', 'created_at']
        read_only_fields = fields
