"""
LinkedIn OpenID Connect authorization-code flow views.

GET  /auth/linkedin/                 → redirect to LinkedIn consent screen
GET  /auth/linkedin/callback/        → receive code from LinkedIn, redirect to frontend
POST /auth/linkedin/exchange-token/  → exchange code, provision user, return JWT cookies + JSON
"""

import logging
from urllib import parse

from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.services.linkedin_oauth import (
    build_authorization_url,
    exchange_code_for_tokens,
    fetch_linkedin_profile,
)
from authentication.services.user_provisioning import (
    ALLOWED_GOOGLE_SIGN_IN_USER_TYPES as ALLOWED_LINKEDIN_SIGN_IN_USER_TYPES,
    build_user_payload,
    generate_jwt_tokens,
    get_or_create_google_user as get_or_create_linkedin_user,
    sync_social_account,
)
from authentication.utils.cookie_helpers import set_jwt_cookies
from authentication.utils.exceptions import (
    GoogleOAuthAccountConflictError as LinkedInOAuthAccountConflictError,
    GoogleOAuthBlockedUserError as LinkedInOAuthBlockedUserError,
    GoogleOAuthCodeExchangeError as LinkedInOAuthCodeExchangeError,
    GoogleOAuthConfigError as LinkedInOAuthConfigError,
    GoogleOAuthError as LinkedInOAuthError,
    GoogleOAuthProfileError as LinkedInOAuthProfileError,
)

logger = logging.getLogger(__name__)


def _frontend_error_url(message):
    base = getattr(settings, 'FRONTEND_ERROR_URL', '')
    if not base:
        base = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000') + '/auth/error'
    return f'{base}?{parse.urlencode({"error": message})}'


def _frontend_callback_url(code, state=''):
    base = getattr(settings, 'FRONTEND_LINKEDIN_CALLBACK', '')
    if not base:
        base = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000') + '/auth/linkedin/callback'
    params = {'code': code}
    if state:
        params['state'] = state
    return f'{base}?{parse.urlencode(params)}'


# 1. GET /auth/linkedin/ — redirect user to LinkedIn consent screen
class LinkedInAuthRedirectView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            url, state = build_authorization_url()
        except LinkedInOAuthConfigError as exc:
            logger.error('LinkedIn OAuth misconfigured: %s', exc)
            return Response(
                {'success': False, 'message': 'LinkedIn sign-in is not configured on the server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        request.session['linkedin_oauth_state'] = state
        user_type = (request.query_params.get('user_type') or 'learner').strip().lower()
        request.session['linkedin_oauth_user_type'] = user_type
        return HttpResponseRedirect(url)


# 2. GET /auth/linkedin/callback/ — receive code, redirect to frontend
class LinkedInAuthCallbackView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        error_param = request.query_params.get('error')
        if error_param:
            logger.info('LinkedIn returned error: %s', error_param)
            frontend_url = getattr(settings, 'FRONTEND_LINKEDIN_CALLBACK', '')
            if frontend_url:
                return HttpResponseRedirect(_frontend_error_url('LinkedIn sign-in was cancelled or failed.'))
            return Response(
                {'success': False, 'message': 'LinkedIn sign-in was cancelled or failed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        code = request.query_params.get('code')
        if not code:
            frontend_url = getattr(settings, 'FRONTEND_LINKEDIN_CALLBACK', '')
            if frontend_url:
                return HttpResponseRedirect(_frontend_error_url('No authorization code received from LinkedIn.'))
            return Response(
                {'success': False, 'message': 'No authorization code received from LinkedIn.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        frontend_url = getattr(settings, 'FRONTEND_LINKEDIN_CALLBACK', '')
        if frontend_url:
            state = request.query_params.get('state', '')
            return HttpResponseRedirect(_frontend_callback_url(code, state))
        return self._handle_exchange(request, code)

    def _handle_exchange(self, request, code):
        user_type = request.session.pop('linkedin_oauth_user_type', 'learner')
        if user_type not in ALLOWED_LINKEDIN_SIGN_IN_USER_TYPES:
            user_type = 'learner'
        try:
            linkedin_tokens = exchange_code_for_tokens(code)
        except LinkedInOAuthCodeExchangeError as exc:
            logger.warning('LinkedIn code exchange failed: %s', exc)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except LinkedInOAuthConfigError as exc:
            logger.error('LinkedIn OAuth misconfigured: %s', exc)
            return Response(
                {'success': False, 'message': 'LinkedIn sign-in is not configured on the server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            profile = fetch_linkedin_profile(linkedin_tokens['access_token'])
        except LinkedInOAuthProfileError as exc:
            logger.warning('LinkedIn profile fetch failed: %s', exc)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            user, is_new_user = get_or_create_linkedin_user(profile, user_type)
        except LinkedInOAuthBlockedUserError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except LinkedInOAuthAccountConflictError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            sync_social_account(user, profile)
        except LinkedInOAuthAccountConflictError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception as exc:
            logger.error('SocialAccount sync failed: %s', exc)
            return Response(
                {'success': False, 'message': 'Unable to link the LinkedIn account right now.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            tokens = generate_jwt_tokens(user)
        except Exception as exc:
            logger.error('JWT generation failed for %s: %s', user.email, exc)
            return Response(
                {'success': False, 'message': 'Unable to generate login tokens right now.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        user_data = build_user_payload(user, is_new_user)
        message = (
            'LinkedIn sign-in successful. New account created.'
            if is_new_user
            else 'LinkedIn sign-in successful.'
        )
        response = Response(
            {'success': True, 'message': message, 'data': user_data},
            status=status.HTTP_200_OK,
        )
        set_jwt_cookies(response, tokens)
        return response


# 3. POST /auth/linkedin/exchange-token/ — exchange code → JWT cookies + JSON
class LinkedInExchangeTokenView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        code = request.data.get('code')
        user_type = (request.data.get('user_type') or 'learner').strip().lower()
        if user_type not in ALLOWED_LINKEDIN_SIGN_IN_USER_TYPES:
            user_type = 'learner'
        try:
            linkedin_tokens = exchange_code_for_tokens(code)
        except LinkedInOAuthCodeExchangeError as exc:
            logger.warning('LinkedIn code exchange failed: %s', exc)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except LinkedInOAuthConfigError as exc:
            logger.error('LinkedIn OAuth misconfigured: %s', exc)
            return Response(
                {'success': False, 'message': 'LinkedIn sign-in is not configured on the server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            profile = fetch_linkedin_profile(linkedin_tokens['access_token'])
        except LinkedInOAuthProfileError as exc:
            logger.warning('LinkedIn profile fetch failed: %s', exc)
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            user, is_new_user = get_or_create_linkedin_user(profile, user_type)
        except LinkedInOAuthBlockedUserError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except LinkedInOAuthAccountConflictError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            sync_social_account(user, profile)
        except LinkedInOAuthAccountConflictError as exc:
            return Response(
                {'success': False, 'message': str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except Exception as exc:
            logger.error('SocialAccount sync failed: %s', exc)
            return Response(
                {'success': False, 'message': 'Unable to link the LinkedIn account right now.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        try:
            tokens = generate_jwt_tokens(user)
        except Exception as exc:
            logger.error('JWT generation failed for %s: %s', user.email, exc)
            return Response(
                {'success': False, 'message': 'Unable to generate login tokens right now.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        user_data = build_user_payload(user, is_new_user)
        message = (
            'LinkedIn sign-in successful. New account created.'
            if is_new_user
            else 'LinkedIn sign-in successful.'
        )
        response = Response(
            {'success': True, 'message': message, 'data': user_data},
            status=status.HTTP_200_OK,
        )
        set_jwt_cookies(response, tokens)
        return response
