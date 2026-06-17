"""Reusable DRF permission classes used across apps."""

from authentication.models import InstructorProfile, PartnerInstitutionProfile
from rest_framework.permissions import BasePermission


class IsPlatformAdmin(BasePermission):
    """Allow access only to platform admin/staff users."""

    message = 'Only administrators can perform this action.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or request.user.user_type == 'admin')
        )


class IsAdminOrReadOnly(BasePermission):
    """Allow read access to everyone and write access only to admins."""

    def has_permission(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return bool(request.user and request.user.is_staff)


class IsEmailVerified(BasePermission):
    """Only allow access to users whose email is verified."""

    message = 'Your email must be verified before accessing this resource.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_email_verified
        )


class IsProfileOwner(BasePermission):
    """Only allow the owner of an object to modify it."""

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'user'):
            return obj.user == request.user
        return obj == request.user


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


class IsLearnerUser(BasePermission):
    """Allow access only to learner accounts."""

    message = 'Only learners can access this resource.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.user_type == 'learner')


class IsVerifiedPartnerInstitution(BasePermission):
    """Allow access only to partner institutions approved by an admin."""

    message = 'Only verified partner institutions can perform this action.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or user.user_type != 'partner_institution':
            return False
        return PartnerInstitutionProfile.objects.filter(
            user_id=user.id, is_verified=True, is_active=True
        ).exists()


class IsVerifiedCourseCreator(BasePermission):
    """Passes if user is a verified instructor OR a verified partner institution."""

    message = 'Only verified instructors or verified partner institutions can perform this action.'

    def has_permission(self, request, view):
        return (
            IsVerifiedInstructor().has_permission(request, view)
            or IsVerifiedPartnerInstitution().has_permission(request, view)
        )
