"""Reusable DRF permission classes used across apps."""

from django.conf import settings
from django.utils import timezone

from authentication.models import InstructorProfile, PartnerInstitutionProfile
from rest_framework.permissions import BasePermission

# Session key stamped by the admin-console login view; kept as a literal here to
# avoid importing admin_console (which imports this module) — single source of
# truth is admin_console.all_views.auth_views.ADMIN_LOGIN_AT_SESSION_KEY.
_ADMIN_LOGIN_AT_SESSION_KEY = 'admin_login_at'


class IsPlatformAdmin(BasePermission):
    """Allow access only to platform admin/staff users."""

    message = 'Only administrators can perform this action.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or request.user.user_type == 'admin')
        )


class IsRecentlyAuthenticatedAdmin(IsPlatformAdmin):
    """
    Admin whose session login is recent enough for a sensitive action.

    Extends the admin gate with a freshness check against
    ``ADMIN_REAUTH_MAX_AGE``: the session must carry a login timestamp no older
    than that window. Wired onto sensitive admin-console endpoints as they are
    built; requires a session login (a JWT-only admin has no timestamp and is
    asked to re-authenticate).
    """

    message = 'Please re-authenticate to perform this action.'

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False

        session = getattr(request, 'session', None)
        login_at = session.get(_ADMIN_LOGIN_AT_SESSION_KEY) if session else None
        if not login_at:
            return False

        max_age = getattr(settings, 'ADMIN_REAUTH_MAX_AGE', 900)
        return (timezone.now().timestamp() - login_at) <= max_age


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


class IsPartnerInstitutionUser(BasePermission):
    """Allow access to any partner-institution account (verification not required)."""

    message = 'Only partner institutions can access this resource.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.user_type == 'partner_institution')


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


class IsCourseCreator(BasePermission):
    """Instructor OR partner-institution account — identity verification NOT required.

    Unverified analog of IsVerifiedCourseCreator; used on course-authoring
    endpoints so a course can be built and tested before identity verification.
    Submission (leaving draft) still requires IsVerifiedCourseCreator.
    """

    message = 'Only instructors or partner institutions can perform this action.'

    def has_permission(self, request, view):
        return (
            IsInstructorUser().has_permission(request, view)
            or IsPartnerInstitutionUser().has_permission(request, view)
        )
