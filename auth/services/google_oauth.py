"""
Google OAuth2 authorization-code flow service.

Handles:
- Building the Google consent URL
- Exchanging an authorization code for tokens (server-to-server)
- Fetching and normalising the Google user profile
"""

import json
import logging
import secrets
from urllib import error, parse, request

from django.conf import settings

from auth.utils.exceptions import (
    GoogleOAuthCodeExchangeError,
    GoogleOAuthConfigError,
    GoogleOAuthProfileError,
)

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'

SCOPES = 'openid email profile'


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _get_client_id():
    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '')
    if not client_id:
        raise GoogleOAuthConfigError('GOOGLE_CLIENT_ID is not configured.')
    return client_id


def _get_client_secret():
    client_secret = getattr(settings, 'GOOGLE_CLIENT_SECRET', '')
    if not client_secret:
        raise GoogleOAuthConfigError('GOOGLE_CLIENT_SECRET is not configured.')
    return client_secret


def _get_callback_url():
    callback_url = getattr(settings, 'GOOGLE_CALLBACK_URL', '')
    if not callback_url:
        raise GoogleOAuthConfigError('GOOGLE_CALLBACK_URL is not configured.')
    return callback_url


# ---------------------------------------------------------------------------
# 1. Build the Google consent/redirect URL
# ---------------------------------------------------------------------------

def build_authorization_url(state=None):
    """Return (authorization_url, state) for a Google OAuth consent redirect."""
    if state is None:
        state = secrets.token_urlsafe(32)

    params = {
        'client_id': _get_client_id(),
        'redirect_uri': _get_callback_url(),
        'response_type': 'code',
        'scope': SCOPES,
        'access_type': 'online',
        'prompt': 'select_account',
        'state': state,
    }
    url = f'{GOOGLE_AUTH_URL}?{parse.urlencode(params)}'
    return url, state


# ---------------------------------------------------------------------------
# 2. Exchange authorization code for tokens
# ---------------------------------------------------------------------------

def exchange_code_for_tokens(code):
    """
    Exchange the authorization code with Google and return the raw token
    response dict (contains access_token, id_token, etc.).
    """
    body = parse.urlencode({
        'code': code,
        'client_id': _get_client_id(),
        'client_secret': _get_client_secret(),
        'redirect_uri': _get_callback_url(),
        'grant_type': 'authorization_code',
    }).encode('utf-8')

    http_request = request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )

    try:
        with request.urlopen(http_request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
    except error.HTTPError as exc:
        try:
            err_body = exc.read().decode('utf-8', errors='replace')
        except Exception:
            err_body = ''
        logger.warning('Google token exchange HTTP %s: %s', exc.code, err_body)
        raise GoogleOAuthCodeExchangeError(
            'Failed to exchange the authorization code with Google.'
        ) from exc
    except error.URLError as exc:
        logger.warning('Google token endpoint unreachable: %s', exc)
        raise GoogleOAuthCodeExchangeError(
            'Unable to reach Google right now. Please try again.'
        ) from exc
    except Exception as exc:
        logger.exception('Unexpected error during Google code exchange.')
        raise GoogleOAuthCodeExchangeError(
            'Failed to exchange the authorization code with Google.'
        ) from exc

    if 'access_token' not in data:
        raise GoogleOAuthCodeExchangeError(
            'Google did not return an access token.'
        )

    return data


# ---------------------------------------------------------------------------
# 3. Fetch Google user profile via userinfo endpoint
# ---------------------------------------------------------------------------

def fetch_google_profile(access_token):
    """
    Fetch the Google user profile from the userinfo endpoint.
    Returns a normalised dict with keys: sub, email, email_verified,
    full_name, given_name, family_name, picture.
    """
    http_request = request.Request(
        GOOGLE_USERINFO_URL,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
        },
    )

    try:
        with request.urlopen(http_request, timeout=10) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise GoogleOAuthProfileError(
                'Invalid access token returned by Google.'
            ) from exc
        logger.warning('Google userinfo endpoint returned HTTP %s.', exc.code)
        raise GoogleOAuthProfileError(
            'Unable to fetch your Google profile right now.'
        ) from exc
    except error.URLError as exc:
        logger.warning('Google userinfo endpoint unreachable: %s', exc)
        raise GoogleOAuthProfileError(
            'Unable to reach Google right now. Please try again.'
        ) from exc
    except Exception as exc:
        logger.exception('Unexpected error fetching Google profile.')
        raise GoogleOAuthProfileError(
            'Unable to fetch your Google profile right now.'
        ) from exc

    profile = _normalize_profile(payload)
    _validate_profile(profile)
    return profile


# ---------------------------------------------------------------------------
# Normalisation / validation helpers
# ---------------------------------------------------------------------------

def _normalize_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == 'true'
    return False


def _normalize_profile(payload):
    sub = payload.get('sub') or payload.get('id')
    email = (payload.get('email') or '').strip().lower()
    email_verified = _normalize_bool(
        payload.get('email_verified', payload.get('verified_email', False))
    )
    full_name = (payload.get('name') or '').strip()

    if not full_name:
        given = (payload.get('given_name') or '').strip()
        family = (payload.get('family_name') or '').strip()
        full_name = f'{given} {family}'.strip()

    return {
        'sub': sub,
        'email': email,
        'email_verified': email_verified,
        'full_name': full_name,
        'given_name': (payload.get('given_name') or '').strip(),
        'family_name': (payload.get('family_name') or '').strip(),
        'picture': payload.get('picture') or '',
    }


def _validate_profile(profile):
    if not profile['sub']:
        raise GoogleOAuthProfileError('Google account identifier (sub) is missing.')

    if not profile['email']:
        raise GoogleOAuthProfileError('Google account email is missing.')

    if not profile['email_verified']:
        raise GoogleOAuthProfileError('Google account email must be verified before sign-in.')
