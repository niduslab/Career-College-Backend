import json
import logging
from urllib import error, request

from django.conf import settings
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

logger = logging.getLogger(__name__)


class GoogleOAuthError(Exception):
    """Base error for Google OAuth verification failures."""


class GoogleTokenValidationError(GoogleOAuthError):
    """Raised when the provided Google token is invalid."""


class GoogleTokenServiceError(GoogleOAuthError):
    """Raised when Google token validation cannot be completed."""


def _get_allowed_audiences():
    audiences = getattr(settings, 'GOOGLE_OAUTH_ALLOWED_CLIENT_IDS', []) or []
    normalized = [audience.strip() for audience in audiences if audience and audience.strip()]
    if not normalized:
        raise GoogleTokenServiceError('Google OAuth is not configured on the server.')
    return normalized


def _normalize_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == 'true'
    return False


def _normalize_google_profile(payload):
    subject = payload.get('sub') or payload.get('id')
    email = (payload.get('email') or '').strip().lower()
    email_verified = _normalize_bool(
        payload.get('email_verified', payload.get('verified_email', False))
    )
    full_name = (payload.get('name') or '').strip()

    if not full_name:
        given_name = (payload.get('given_name') or '').strip()
        family_name = (payload.get('family_name') or '').strip()
        full_name = f'{given_name} {family_name}'.strip()

    return {
        'sub': subject,
        'email': email,
        'email_verified': email_verified,
        'full_name': full_name,
        'given_name': (payload.get('given_name') or '').strip(),
        'family_name': (payload.get('family_name') or '').strip(),
        'picture': payload.get('picture') or '',
        'aud': payload.get('aud'),
    }


def _validate_required_profile_fields(profile):
    if not profile['sub']:
        raise GoogleTokenValidationError('Google account identifier is missing from the token.')

    if not profile['email']:
        raise GoogleTokenValidationError('Google account email is missing from the token.')

    if not profile['email_verified']:
        raise GoogleTokenValidationError('Google account email must be verified before sign-in.')


def _verify_id_token(raw_token, allowed_audiences):
    try:
        payload = id_token.verify_oauth2_token(
            raw_token,
            GoogleRequest(),
            audience=None,
        )
    except ValueError as exc:
        raise GoogleTokenValidationError('Invalid Google ID token.') from exc
    except Exception as exc:
        logger.exception('Unexpected Google ID token verification failure.')
        raise GoogleTokenServiceError('Unable to validate the Google token right now.') from exc

    audience = payload.get('aud')
    if audience not in allowed_audiences:
        raise GoogleTokenValidationError('Google token audience does not match this application.')

    profile = _normalize_google_profile(payload)
    _validate_required_profile_fields(profile)
    return profile


def _fetch_google_userinfo(access_token_value):
    http_request = request.Request(
        'https://openidconnect.googleapis.com/v1/userinfo',
        headers={
            'Authorization': f'Bearer {access_token_value}',
            'Accept': 'application/json',
        },
    )

    try:
        with request.urlopen(http_request, timeout=10) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise GoogleTokenValidationError('Invalid Google access token.') from exc
        logger.warning('Google userinfo endpoint returned HTTP %s.', exc.code)
        raise GoogleTokenServiceError('Unable to validate the Google token right now.') from exc
    except error.URLError as exc:
        logger.warning('Google userinfo endpoint is unreachable: %s', exc)
        raise GoogleTokenServiceError('Unable to reach Google token validation service right now.') from exc
    except Exception as exc:
        logger.exception('Unexpected Google access token verification failure.')
        raise GoogleTokenServiceError('Unable to validate the Google token right now.') from exc

    profile = _normalize_google_profile(payload)
    _validate_required_profile_fields(profile)
    return profile


def verify_google_token(raw_token, token_type='auto'):
    token = (raw_token or '').strip()
    if not token:
        raise GoogleTokenValidationError('Google token is required.')

    allowed_audiences = _get_allowed_audiences()
    normalized_type = (token_type or 'auto').strip().lower()

    if normalized_type not in {'auto', 'id_token', 'access_token'}:
        raise GoogleTokenValidationError('Unsupported Google token type.')

    if normalized_type in {'auto', 'id_token'}:
        try:
            return _verify_id_token(token, allowed_audiences)
        except GoogleTokenValidationError:
            if normalized_type == 'id_token':
                raise

    if normalized_type in {'auto', 'access_token'}:
        return _fetch_google_userinfo(token)

    raise GoogleTokenValidationError('Unable to verify the provided Google token.')