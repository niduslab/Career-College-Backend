"""
Certificate endpoints.

Routes (all under /api/v1/courses/):
    GET  my-courses/<slug>/certificate/                      -> LearnerCertificateView
    GET  certificates/<uuid:certificate_uid>/verify/         -> CertificateVerifyView
    GET  certificates/<uuid:certificate_uid>/download/       -> CertificateDownloadView
"""

import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified, IsLearnerUser
from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse
from courses.all_serializers.certificate_serializers import (
    CertificateSerializer,
    PublicCertificateSerializer,
)
from courses.certificate_pdf import generate_certificate_pdf
from courses.services.certificate_service import (
    get_certificate_by_uid,
    get_certificate_for_learner,
)

logger = logging.getLogger(__name__)


class LearnerCertificateView(APIView):
    """
    GET /api/v1/courses/my-courses/<slug>/certificate/

    Authenticated learner fetches their own certificate for a course.
    Slug identifier → 403 when not enrolled (project access-denied policy).
    404 when enrolled but course not yet completed.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request, slug):
        try:
            certificate = get_certificate_for_learner(request.user, slug)
        except NidusCourse.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Course not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except PermissionError:
            return Response(
                {'success': False, 'message': 'You are not enrolled in this course.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        except Certificate.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Certificate not yet issued. Complete the course first.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                'success': True,
                'message': 'Certificate retrieved.',
                'data': CertificateSerializer(certificate).data,
            },
            status=status.HTTP_200_OK,
        )


class CertificateVerifyView(APIView):
    """
    GET /api/v1/courses/certificates/<uuid:certificate_uid>/verify/

    Public (AllowAny). Returns metadata for the certificate if it exists.
    UUID identifier → 404 when not found (never leaks existence of other certs).
    """

    permission_classes = [AllowAny]

    def get(self, request, certificate_uid):
        try:
            certificate = get_certificate_by_uid(certificate_uid)
        except Certificate.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Certificate not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                'success': True,
                'message': 'Certificate is valid.',
                'data': PublicCertificateSerializer(certificate).data,
            },
            status=status.HTTP_200_OK,
        )


class CertificateDownloadView(APIView):
    """
    GET /api/v1/courses/certificates/<uuid:certificate_uid>/download/

    Public (AllowAny). Anyone with the UUID can download the PDF.
    Returns application/pdf as a file attachment.
    """

    permission_classes = [AllowAny]

    def get(self, request, certificate_uid):
        try:
            certificate = get_certificate_by_uid(certificate_uid)
        except Certificate.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Certificate not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            pdf_bytes = generate_certificate_pdf(certificate)
        except Exception:
            logger.exception('PDF generation failed for certificate=%s', certificate_uid)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = (
            f'attachment; filename="certificate-{certificate.certificate_uid}.pdf"'
        )
        return response
