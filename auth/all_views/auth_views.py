import logging

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from auth.serializers import UserRegistrationSerializer, UserLoginSerializer, LogoutSerializer
from auth.utils import send_otp_email

logger = logging.getLogger(__name__)


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
        print(f"Generated OTP for {user.email}: {otp_code}")  # Debugging log

        try:
            email_sent = send_otp_email(user, otp_code, purpose='registration')
        except Exception as e:
            logger.error(f"Failed to send OTP email to {user.email}: {e}")
            email_sent = False

        if not email_sent:
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

        return Response(
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
                'tokens': tokens,
            },
            status=status.HTTP_200_OK,
        )


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

        return Response(
            {
                'success': True,
                'message': 'Logged out successfully.',
            },
            status=status.HTTP_200_OK,
        )


class TokenRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])

        return Response(
            {
                'success': True,
                'message': 'Token refreshed successfully.',
                'tokens': serializer.validated_data,
            },
            status=status.HTTP_200_OK,
        )