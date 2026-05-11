"""
LinkedIn OpenID Connect authorization-code flow service.

Handles:
- Building the LinkedIn consent URL
- Exchanging an authorization code for tokens (server-to-server)
- Fetching and normalising the LinkedIn user profile
"""

import json
import logging
import secrets
from urllib import error, parse, request

from django.conf import settings

from authentication.utils.exceptions import (
    GoogleOAuthCodeExchangeError as LinkedInOAuthCodeExchangeError,
    GoogleOAuthConfigError as LinkedInOAuthConfigError,
    GoogleOAuthProfileError as LinkedInOAuthProfileError,
)

logger = logging.getLogger(__name__)

LINKEDIN_AUTH_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LINKEDIN_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LINKEDIN_USERINFO_URL = 'https://api.linkedin.com/v2/me'
LINKEDIN_EMAIL_URL = 'https://api.linkedin.com/v2/emailAddress?q=members&projection=(elements*(handle~))'

SCOPES = 'openid email profile'


def _get_client_id():
    client_id = getattr(settings, 'LINKEDIN_CLIENT_ID', '')
    if not client_id:
        raise LinkedInOAuthConfigError('LINKEDIN_CLIENT_ID is not configured.')
    return client_id


def _get_client_secret():
    client_secret = getattr(settings, 'LINKEDIN_CLIENT_SECRET', '')
    if not client_secret:
        raise LinkedInOAuthConfigError('LINKEDIN_CLIENT_SECRET is not configured.')
    return client_secret


def _get_callback_url():
    callback_url = getattr(settings, 'LINKEDIN_CALLBACK_URL', '')
    if not callback_url:
        raise LinkedInOAuthConfigError('LINKEDIN_CALLBACK_URL is not configured.')
    return callback_url


def build_authorization_url(state=None):
    """Return (authorization_url, state) for a LinkedIn OAuth consent redirect."""
    if state is None:
        state = secrets.token_urlsafe(32)

    params = {
        'client_id': _get_client_id(),
        'redirect_uri': _get_callback_url(),
        'response_type': 'code',
        'scope': SCOPES,
        'state': state,
    }
    url = f'{LINKEDIN_AUTH_URL}?{parse.urlencode(params)}'
    return url, state


def exchange_code_for_tokens(code):
    """
    Exchange the authorization code with LinkedIn and return the raw token
    response dict (contains access_token, id_token, etc.).
    """
    body = parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': _get_callback_url(),
        'client_id': _get_client_id(),
        'client_secret': _get_client_secret(),
    }).encode('utf-8')

    http_request = request.Request(
        LINKEDIN_TOKEN_URL,
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
        logger.warning('LinkedIn token exchange HTTP %s: %s', exc.code, err_body)
        raise LinkedInOAuthCodeExchangeError(
            'Failed to exchange the authorization code with LinkedIn.'
        ) from exc
    except error.URLError as exc:
        logger.warning('LinkedIn token endpoint unreachable: %s', exc)
        raise LinkedInOAuthCodeExchangeError(
            'Unable to reach LinkedIn right now. Please try again.'
        ) from exc
    except Exception as exc:
        logger.error('LinkedIn token exchange error: %s', exc)
        raise LinkedInOAuthCodeExchangeError('Unknown error during LinkedIn token exchange.') from exc
    if 'access_token' not in data:
        logger.warning('LinkedIn token exchange failed: %s', data)
        raise LinkedInOAuthCodeExchangeError('No access token received from LinkedIn.')
    return data


def fetch_linkedin_profile(access_token):
    """
    Fetch and normalize the LinkedIn user profile using the access token.
    """
    headers = {'Authorization': f'Bearer {access_token}'}
    # Fetch basic profile
    req = request.Request(LINKEDIN_USERINFO_URL, headers=headers)
    try:
        with request.urlopen(req, timeout=10) as response:
            profile = json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        logger.warning('LinkedIn profile fetch failed: %s', exc)
        raise LinkedInOAuthProfileError('Failed to fetch LinkedIn profile.') from exc
    # Fetch email
    req_email = request.Request(LINKEDIN_EMAIL_URL, headers=headers)
    try:
        with request.urlopen(req_email, timeout=10) as response:
            email_data = json.loads(response.read().decode('utf-8'))
            email = email_data['elements'][0]['handle~']['emailAddress']
    except Exception as exc:
        logger.warning('LinkedIn email fetch failed: %s', exc)
        email = None
    profile['email'] = email
    return profile
