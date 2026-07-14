import logging

from django.conf import settings
from django.contrib.auth import login as django_login
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from authentication.serializers import (
    LogoutSerializer,
    UserLoginSerializer,
    UserRegistrationSerializer,
)
from authentication.tasks import send_otp_email_task
from authentication.utils.cookie_helpers import delete_jwt_cookies, set_jwt_cookies

logger = logging.getLogger(__name__)

_LOGIN_RATE_LIMIT = getattr(settings, 'LOGIN_RATE_LIMIT', '10/min')


class LoginThrottle(AnonRateThrottle):
    scope = 'login'
    rate = _LOGIN_RATE_LIMIT


class UserRegistrationView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = UserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'Registration failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = serializer.save()
        except Exception as e:
            logger.error(f"Registration failed during user creation: {e}")
            return Response(
                {
                    'success': False,
                    'message': 'Registration failed due to a server error. Please try again.',
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # OTP is already generated in serializer.create(); only send it here.
        otp_code = user.otp_code

        # OTP email is sent asynchronously; only a broker-enqueue failure is
        # surfaced synchronously (the worker handles SMTP + retries).
        try:
            send_otp_email_task.delay(user.pk, otp_code, 'registration')
        except Exception as e:
            logger.error(f"Failed to enqueue OTP email for {user.email}: {e}")
            return Response({
                'success': False,
                'user_created': True,
                'otp_sent': False,
                'message': 'User registered but failed to send OTP email. Please use resend OTP.',
                'email': user.email,
                'user_id': user.id
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        response_data = {
            'user_id': user.pk,
            'email': user.email,
            'full_name': user.full_name,
            'user_type': user.user_type,
            'is_email_verified': user.is_email_verified,
            'is_verified': user.is_verified,
        }

        if user.user_type == 'partner_institution' and hasattr(user, 'partner_institution_profile'):
            response_data['institution_name'] = user.partner_institution_profile.institution_name
            response_data['institution_type'] = user.partner_institution_profile.institution_type

        return Response(
            {
                'success': True,
                'message': 'Registration successful. OTP sent to your email.',
                'data': response_data,
            },
            status=status.HTTP_201_CREATED,
        )


class UserLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        serializer = UserLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'Login failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = serializer.validated_data['user']

        try:
            tokens = serializer.get_tokens(user)
        except Exception as e:
            logger.error(f"Token generation failed for {user.email}: {e}")
            return Response(
                {
                    'success': False,
                    'message': 'Login failed due to a server error. Please try again.',
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Admins additionally get a server-side session + csrf cookie
        if user.is_staff or user.user_type == 'admin':
            django_login(request, user)
            request.session['admin_login_at'] = timezone.now().timestamp()
            get_token(request)  # primes the csrftoken cookie on the response

        response = Response(
            {
                'success': True,
                'message': 'Login successful.',
                'data': {
                    'user_id': user.pk,
                    'email': user.email,
                    'full_name': user.full_name,
                    'user_type': user.user_type,
                    'is_email_verified': user.is_email_verified,
                },
            },
            status=status.HTTP_200_OK,
        )
        set_jwt_cookies(response, tokens)
        return response


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'Logout failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.save()
        except Exception as e:
            logger.error(f"Logout failed for {request.user.email}: {e}")
            return Response(
                {
                    'success': False,
                    'message': 'Logout failed. Token may already be blacklisted.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        response = Response(
            {
                'success': True,
                'message': 'Logged out successfully.',
            },
            status=status.HTTP_200_OK,
        )
        delete_jwt_cookies(response)
        return response


class TokenRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'Token refresh failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.validated_data  # triggers token validation
        except TokenError as e:
            return Response(
                {
                    'success': False,
                    'message': str(e),
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(
            {
                'success': True,
                'message': 'Token refreshed successfully.',
                'tokens': serializer.validated_data,
            },
            status=status.HTTP_200_OK,
        )
