"""
Service layer for Google OAuth user provisioning and SocialAccount linking.

Separated from the HTTP/view layer for testability.
"""

import logging

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.utils.exceptions import (
    GoogleOAuthAccountConflictError,
    GoogleOAuthBlockedUserError,
)

logger = logging.getLogger(__name__)

User = get_user_model()

ALLOWED_GOOGLE_SIGN_IN_USER_TYPES = ('learner', 'instructor')


def get_or_create_google_user(profile, user_type='learner'):
    """
    Given a normalised Google profile dict, return (user, is_new_user).

    - New users: create with set_unusable_password(), auto-verified email.
    - Existing users: match by email case-insensitively; reject blocked users.
    """
    email = profile['email']
    user = User.objects.all_with_deleted().filter(email__iexact=email).first()

    if user:
        _check_existing_user(user)
        _update_existing_user(user, profile)
        return user, False

    user = _create_google_user(profile, user_type)
    return user, True


def sync_social_account(user, profile):
    """
    Link or update the SocialAccount for the given user + Google profile.

    Raises GoogleOAuthAccountConflictError if:
      - The Google sub is already linked to a different user.
      - The user is already linked to a different Google sub.
    """
    sub = profile['sub']

    existing_by_sub = SocialAccount.objects.filter(
        provider='google', uid=sub,
    ).first()

    if existing_by_sub and existing_by_sub.user_id != user.pk:
        raise GoogleOAuthAccountConflictError(
            'This Google account is already linked to another user.'
        )

    existing_by_user = SocialAccount.objects.filter(
        provider='google', user=user,
    ).first()

    if existing_by_user and existing_by_user.uid != sub:
        raise GoogleOAuthAccountConflictError(
            'Your account is already linked to a different Google account.'
        )

    extra_data = {
        'email': profile['email'],
        'email_verified': profile['email_verified'],
        'name': profile['full_name'],
        'given_name': profile['given_name'],
        'family_name': profile['family_name'],
        'picture': profile['picture'],
    }

    SocialAccount.objects.update_or_create(
        provider='google',
        uid=sub,
        defaults={
            'user': user,
            'extra_data': extra_data,
        },
    )


def generate_jwt_tokens(user):
    """Return a dict with 'access' and 'refresh' JWT strings."""
    refresh = RefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }


def build_user_payload(user, is_new_user):
    """Build the JSON-safe user dict returned to the frontend."""
    return {
        'user_id': user.pk,
        'email': user.email,
        'full_name': user.full_name,
        'user_type': user.user_type,
        'is_email_verified': user.is_email_verified,
        'is_verified': user.is_verified,
        'auth_provider': 'google',
        'is_new_user': is_new_user,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_existing_user(user):
    if user.is_deleted:
        raise GoogleOAuthBlockedUserError(
            'This account has been deleted and cannot sign in with Google.'
        )
    if not user.is_active or user.is_restricted_by_admin:
        raise GoogleOAuthBlockedUserError(
            'Your account has been deactivated or restricted. Please contact support.'
        )
    if user.user_type == 'partner_institution':
        raise GoogleOAuthBlockedUserError(
            'Partner institution accounts cannot sign in with Google.'
        )


def _update_existing_user(user, profile):
    fields_to_update = []

    if not user.is_email_verified:
        user.is_email_verified = True
        fields_to_update.append('is_email_verified')

    if not user.full_name and profile['full_name']:
        user.full_name = profile['full_name']
        fields_to_update.append('full_name')

    if fields_to_update:
        user.save(update_fields=fields_to_update + ['updated_at'])


def _create_google_user(profile, user_type):
    full_name = profile['full_name'] or profile['email'].split('@')[0]

    try:
        user = User(
            email=profile['email'],
            full_name=full_name,
            user_type=user_type,
            is_email_verified=True,
            is_verified=(user_type == 'learner'),
        )
        user.set_unusable_password()
        user.save()
    except IntegrityError:
        raise GoogleOAuthAccountConflictError(
            'A user with this email already exists.'
        )

    return user
