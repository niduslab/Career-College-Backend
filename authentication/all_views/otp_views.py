import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle
from authentication.serializers import VerifyOTPSerializer, ResendOTPSerializer
from authentication.utils import send_otp_email
from django.conf import settings

logger = logging.getLogger(__name__)

OTP_RATE_LIMIT = getattr(settings, 'OTP_RATE_LIMIT', '20/min')


class OTPGenerateThrottle(AnonRateThrottle):
    """Rate limit for OTP generation: 20 requests per minute"""
    rate = OTP_RATE_LIMIT


class OTPVerifyThrottle(AnonRateThrottle):
    """Rate limit for OTP verification: 20 requests per minute"""
    rate = OTP_RATE_LIMIT


class VerifyOTPView(APIView):
    """Verify OTP - supports both registration and password reset"""
    permission_classes = [AllowAny]
    throttle_classes = [OTPVerifyThrottle]  
    
    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'OTP verification failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = serializer.save()
            user = serializer.validated_data['user']
            purpose = result.get('purpose', serializer.validated_data.get('purpose', 'registration'))

            response_data = {
                'success': True,
                'message': result.get('message', 'OTP verified successfully.'),
                'data': {
                    'user_id': user.id,
                    'email': user.email,
                    'purpose': purpose,
                },
            }

            if purpose == 'registration':
                response_data['message'] = 'Email verified successfully! You can now login.'

            if purpose == 'password_reset':
                response_data['message'] = 'OTP verified successfully! Now you can reset your password.'
                response_data['data']['reset_token'] = result.get('password_reset_token')
                response_data['data']['token_expires_in'] = '15 minutes'
                response_data['data']['note'] = 'Use this token with email to reset your password within 15 minutes.'

            return Response(response_data, status=status.HTTP_200_OK)
        except serializers.ValidationError as exc:
            return Response({
                'success': False,
                'message': exc.detail
            }, status=status.HTTP_400_BAD_REQUEST)


class ResendOTPView(APIView):
    """Resend OTP - supports both registration and password reset"""
    permission_classes = [AllowAny]
    throttle_classes = [OTPGenerateThrottle]  
    
    def post(self, request):
        serializer = ResendOTPSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'OTP resend failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.save()
            user = serializer.validated_data['user']
            purpose = serializer.validated_data.get('purpose', 'registration')
            otp_code = user.otp_code
            print(f"Generated OTP for {user.email} (purpose={purpose}): {otp_code}")

            email_sent = send_otp_email(user, otp_code, purpose=purpose)
            if not email_sent:
                logger.error(f'OTP resend email failed for {user.email} purpose={purpose}')
                return Response({
                    'error': True,
                    'message': 'OTP generated but failed to send email. Please try again later.'
                }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

            return Response({
                'success': True,
                'message': f'OTP has been resent to {user.email}',
                'data': {
                    'user_id': user.id,
                    'email': user.email,
                    'purpose': purpose,
                    'note': 'OTP will expire in 2 minutes.'
                }
            }, status=status.HTTP_200_OK)
        except serializers.ValidationError as exc:
            return Response({
                'error': True,
                'message': exc.detail
            }, status=status.HTTP_400_BAD_REQUEST)
