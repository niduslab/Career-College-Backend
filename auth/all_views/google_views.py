"""
Google OAuth2 authorization-code flow views.

GET  /auth/google/                 → redirect to Google consent screen
GET  /auth/google/callback/        → receive code from Google, redirect to frontend
POST /auth/google/exchange-token/  → exchange code, provision user, return JWT cookies + JSON
"""

import logging
from urllib import parse

from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from auth.services.google_oauth import (
    build_authorization_url,
    exchange_code_for_tokens,
    fetch_google_profile,
)
from auth.services.user_provisioning import (
    ALLOWED_GOOGLE_SIGN_IN_USER_TYPES,
    build_user_payload,
    generate_jwt_tokens,
    get_or_create_google_user,
    sync_social_account,
)
from auth.utils.cookie_helpers import set_jwt_cookies
from auth.utils.exceptions import (
    GoogleOAuthAccountConflictError,
    GoogleOAuthBlockedUserError,
    GoogleOAuthCodeExchangeError,
    GoogleOAuthConfigError,
    GoogleOAuthError,
    GoogleOAuthProfileError,
)

logger = logging.getLogger(__name__)


def _frontend_error_url(message):
    """Build the URL the frontend should land on when Google sign-in fails."""
    base = getattr(settings, 'FRONTEND_ERROR_URL', '')
    if not base:
        base = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000') + '/auth/error'
    return f'{base}?{parse.urlencode({"error": message})}'


def _frontend_callback_url(code, state=''):
    """Build the frontend callback URL carrying only the authorization code."""
    base = getattr(settings, 'FRONTEND_GOOGLE_CALLBACK', '')
    if not base:
        base = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000') + '/auth/google/callback'
    params = {'code': code}
    if state:
        params['state'] = state
    return f'{base}?{parse.urlencode(params)}'


# ---------------------------------------------------------------------------
# 1. GET /auth/google/ — redirect user to Google consent screen
# ---------------------------------------------------------------------------

class GoogleAuthRedirectView(APIView):
    """Redirect the user's browser to Google's OAuth consent screen."""
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            url, state = build_authorization_url()
        except GoogleOAuthConfigError as exc:
            logger.error('Google OAuth misconfigured: %s', exc)
            return Response(
                {'success': False, 'message': 'Google sign-in is not configured on the server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        request.session['google_oauth_state'] = state

        # Persist user_type so the callback can use it in backend-only mode
        user_type = (request.query_params.get('user_type') or 'learner').strip().lower()
        request.session['google_oauth_user_type'] = user_type

        return HttpResponseRedirect(url)


# ---------------------------------------------------------------------------
# 2. GET /auth/google/callback/ — receive code, redirect to frontend
# ---------------------------------------------------------------------------

class GoogleAuthCallbackView(APIView):
    """
    Google redirects here after consent.

    **Frontend mode** (``FRONTEND_GOOGLE_CALLBACK`` is set):
        Forward the authorization code to the frontend via redirect.

    **Backend-only mode** (no frontend URL configured):
        Exchange the code, provision the user, set HttpOnly JWT cookies,
        and return a JSON response directly.  ``user_type`` defaults to
        ``learner``; pass ``?user_type=instructor`` on the initial
        ``/auth/google/?user_type=instructor`` redirect to override.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        error_param = request.query_params.get('error')
        if error_param:
            logger.info('Google returned error: %s', error_param)
            frontend_url = getattr(settings, 'FRONTEND_GOOGLE_CALLBACK', '')
            if frontend_url:
                return HttpResponseRedirect(
                    _frontend_error_url('Google sign-in was cancelled or failed.')
                )
            return Response(
                {'success': False, 'message': 'Google sign-in was cancelled or failed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        code = request.query_params.get('code')
        if not code:
            frontend_url = getattr(settings, 'FRONTEND_GOOGLE_CALLBACK', '')
            if frontend_url:
                return HttpResponseRedirect(
                    _frontend_error_url('No authorization code received from Google.')
                )
            return Response(
                {'success': False, 'message': 'No authorization code received from Google.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Frontend mode: redirect with the code ---
        frontend_url = getattr(settings, 'FRONTEND_GOOGLE_CALLBACK', '')
        if frontend_url:
            state = request.query_params.get('state', '')
            return HttpResponseRedirect(_frontend_callback_url(code, state))

        # --- Backend-only mode: do the full exchange here ---
        return self._handle_exchange(request, code)

    def _handle_exchange(self, request, code):
        """Full code-exchange flow when no frontend is available."""
        # Recover user_type from the session (set during the redirect step)
        user_type = request.session.pop('google_oauth_user_type', 'learner')
        if user_type not in ALLOWED_GOOGLE_SIGN_IN_USER_TYPES:
            user_type = 'learner'

        # Step 1: Exchange authorization code with Google
        try:
            google_tokens = exchange_code_for_tokens(code)
        except GoogleOAuthCodeExchangeError as exc:
            logger.warning('Google code exchange failed: %s', exc)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except GoogleOAuthConfigError as exc:
            logger.error('Google OAuth misconfigured: %s', exc)
            return Response(
                {'success': False, 'message': 'Google sign-in is not configured on the server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Step 2: Fetch Google profile
        try:
            profile = fetch_google_profile(google_tokens['access_token'])
        except GoogleOAuthProfileError as exc:
            logger.warning('Google profile fetch failed: %s', exc)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Step 3: Provision / find user
        try:
            user, is_new_user = get_or_create_google_user(profile, user_type)
        except GoogleOAuthBlockedUserError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except GoogleOAuthAccountConflictError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        # Step 4: Link SocialAccount
        try:
            sync_social_account(user, profile)
        except GoogleOAuthAccountConflictError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception as exc:
            logger.error('SocialAccount sync failed: %s', exc)
            return Response(
                {'success': False, 'message': 'Unable to link the Google account right now.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Step 5: Generate JWTs
        try:
            tokens = generate_jwt_tokens(user)
        except Exception as exc:
            logger.error('JWT generation failed for %s: %s', user.email, exc)
            return Response(
                {'success': False, 'message': 'Unable to generate login tokens right now.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Step 6: Build response with HttpOnly cookies
        user_data = build_user_payload(user, is_new_user)
        message = (
            'Google sign-in successful. New account created.'
            if is_new_user
            else 'Google sign-in successful.'
        )

        response = Response(
            {'success': True, 'message': message, 'data': user_data},
            status=status.HTTP_200_OK,
        )
        set_jwt_cookies(response, tokens)
        return response


# ---------------------------------------------------------------------------
# 3. POST /auth/google/exchange-token/ — exchange code → JWT cookies + JSON
# ---------------------------------------------------------------------------

class GoogleExchangeTokenView(APIView):
    """
    The frontend POSTs the authorization code here.

    We exchange it with Google server-to-server, fetch the profile,
    create/update the user, link SocialAccount, generate SimpleJWT tokens,
    set HttpOnly cookies, and return a JSON user payload.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        code = (request.data.get('code') or '').strip()
        if not code:
            return Response(
                {'success': False, 'message': 'Authorization code is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_type = (request.data.get('user_type') or 'learner').strip().lower()
        if user_type not in ALLOWED_GOOGLE_SIGN_IN_USER_TYPES:
            return Response(
                {
                    'success': False,
                    'message': 'Google sign-in failed.',
                    'errors': {
                        'user_type': [
                            f'"{user_type}" is not a valid choice. '
                            f'Allowed: {", ".join(ALLOWED_GOOGLE_SIGN_IN_USER_TYPES)}.'
                        ]
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Step 1: Exchange authorization code with Google ---
        try:
            google_tokens = exchange_code_for_tokens(code)
        except GoogleOAuthCodeExchangeError as exc:
            logger.warning('Google code exchange failed: %s', exc)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except GoogleOAuthConfigError as exc:
            logger.error('Google OAuth misconfigured: %s', exc)
            return Response(
                {'success': False, 'message': 'Google sign-in is not configured on the server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # --- Step 2: Fetch Google profile ---
        try:
            profile = fetch_google_profile(google_tokens['access_token'])
        except GoogleOAuthProfileError as exc:
            logger.warning('Google profile fetch failed: %s', exc)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Step 3: Provision / find user ---
        try:
            user, is_new_user = get_or_create_google_user(profile, user_type)
        except GoogleOAuthBlockedUserError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except GoogleOAuthAccountConflictError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        # --- Step 4: Link SocialAccount ---
        try:
            sync_social_account(user, profile)
        except GoogleOAuthAccountConflictError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception as exc:
            logger.error('SocialAccount sync failed: %s', exc)
            return Response(
                {'success': False, 'message': 'Unable to link the Google account right now.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- Step 5: Generate JWTs ---
        try:
            tokens = generate_jwt_tokens(user)
        except Exception as exc:
            logger.error('JWT generation failed for %s: %s', user.email, exc)
            return Response(
                {'success': False, 'message': 'Unable to generate login tokens right now.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # --- Step 6: Build response with HttpOnly cookies ---
        user_data = build_user_payload(user, is_new_user)
        message = (
            'Google sign-in successful. New account created.'
            if is_new_user
            else 'Google sign-in successful.'
        )

        response = Response(
            {
                'success': True,
                'message': message,
                'data': user_data,
            },
            status=status.HTTP_200_OK,
        )

        set_jwt_cookies(response, tokens)
        return response
