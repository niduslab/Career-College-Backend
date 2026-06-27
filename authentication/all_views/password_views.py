import logging

from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from authentication.serializers import (
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
)
from authentication.tasks import send_otp_email_task
from django.conf import settings

logger = logging.getLogger(__name__)

OTP_RATE_LIMIT = getattr(settings, 'OTP_RATE_LIMIT', '20/min')


class ForgotPasswordThrottle(AnonRateThrottle):
    rate = OTP_RATE_LIMIT


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ForgotPasswordThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'Password reset request failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        _GENERIC_RESPONSE = Response(
            {
                'success': True,
                'message': 'A password reset code has been sent to your email.',
            },
            status=status.HTTP_200_OK,
        )

        try:
            user = serializer.save()
            if user:
                # Sent asynchronously; response is generic regardless (avoids
                # user enumeration), so a send failure is only logged.
                try:
                    send_otp_email_task.delay(user.pk, user.otp_code, 'password_reset')
                except Exception as e:
                    logger.error(f'Failed to enqueue password reset OTP for user {user.id}: {e}')
        except serializers.ValidationError as exc:
            return Response({'success': False, 'errors': exc.detail}, status=status.HTTP_400_BAD_REQUEST)

        return _GENERIC_RESPONSE


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'Password reset failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = serializer.save()

            return Response(
                {
                    'success': True,
                    'message': 'Password has been reset successfully. You can now login with your new password.',
                    'data': {
                        'email': user.email,
                        'user_id': user.id,
                        'user_slug': user.name_slug,
                    },
                },
                status=status.HTTP_200_OK,
            )
        except serializers.ValidationError as exc:
            return Response({'success': False, 'errors': exc.detail}, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'message': 'Password change failed.',
                    'errors': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            serializer.save()

            return Response(
                {
                    'success': True,
                    'message': 'Password updated successfully.',
                },
                status=status.HTTP_200_OK,
            )
        except serializers.ValidationError as exc:
            return Response({'success': False, 'errors': exc.detail}, status=status.HTTP_400_BAD_REQUEST)
