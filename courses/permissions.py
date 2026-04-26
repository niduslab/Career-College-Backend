from rest_framework.permissions import BasePermission

from auth.models import InstructorProfile


class IsInstructorUser(BasePermission):
    """Allow access only to instructor accounts."""

    message = 'Only instructors can access this resource.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.user_type == 'instructor')


class IsVerifiedInstructor(BasePermission):
    """Allow access only to instructors approved through identity verification."""

    message = 'Only verified instructors can perform this action.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or user.user_type != 'instructor':
            return False

        return InstructorProfile.objects.filter(user_id=user.id, is_verified=True).exists()


class IsCourseInstructor(BasePermission):
    """Object-level guard for courses where user is listed as an instructor."""

    message = 'You must be an assigned instructor for this course.'

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return obj.instructors.filter(pk=user.pk).exists()
