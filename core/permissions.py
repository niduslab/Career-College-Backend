"""Reusable DRF permission classes used across apps."""

from rest_framework.permissions import BasePermission


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
