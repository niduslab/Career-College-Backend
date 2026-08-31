"""
Certificate endpoints.

Routes (all under /api/v1/courses/):
    GET  my-certificates/                                    -> MyCertificateListView
    GET  my-courses/<slug>/certificate/                      -> LearnerCertificateView
    GET  certificates/<uuid:certificate_uid>/verify/         -> CertificateVerifyView
    GET  certificates/<uuid:certificate_uid>/download/       -> CertificateDownloadView
    POST certificates/<uuid:certificate_uid>/revoke/         -> CertificateRevokeView
    POST certificates/<uuid:certificate_uid>/restore/        -> CertificateRestoreView
    GET  certificates/verify/<str:identifier>/               -> CertificatePublicVerifyView
    GET  admin/certificates/                                 -> AdminCertificateListView
"""

import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsLearnerUser, IsPlatformAdmin
from courses.all_models.certificate_models import Certificate
from courses.all_models.course_models import NidusCourse
from courses.all_serializers.certificate_serializers import (
    AdminCertificateListSerializer,
    CertificateSerializer,
    LearnerCertificateListSerializer,
    PublicCertificateSerializer,
)
from courses.certificate_pdf import generate_certificate_pdf
from courses.services.certificate_service import (
    CertificateError,
    get_certificate_by_public_id,
    get_certificate_by_uid,
    get_certificate_for_learner,
    get_learner_certificates,
    restore_certificate,
    revoke_certificate,
    search_certificates,
)

logger = logging.getLogger(__name__)


def _verification_payload(certificate):
    """Envelope for a public verification response, valid or revoked."""
    is_valid = certificate.status == Certificate.Status.VALID
    return {
        'success': True,
        'message': 'Certificate is valid.' if is_valid else 'This certificate has been revoked.',
        'data': PublicCertificateSerializer(certificate).data,
    }


class MyCertificateListView(APIView):
    """
    GET /api/v1/courses/my-certificates/

    Paginated list of the authenticated learner's issued certificates, newest
    first. Two queries total — the course card is joined via select_related so
    there is no per-row lookup.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsLearnerUser]

    def get(self, request):
        queryset = get_learner_certificates(request.user)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = LearnerCertificateListSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


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
    A revoked certificate still returns 200 — the verdict is in `status`, since
    "this credential exists but is revoked" is the answer a verifier needs.
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
        return Response(_verification_payload(certificate), status=status.HTTP_200_OK)


class CertificatePublicVerifyView(APIView):
    """
    GET /api/v1/courses/certificates/verify/<str:identifier>/

    Public (AllowAny). Accepts either the human-readable certificate ID
    (CC-2026-NEXT-000123, what is printed on the certificate and encoded in the
    QR code) or the UUID, so a verifier can paste whichever they hold.
    Unknown identifier → 404, same message either way.
    """

    permission_classes = [AllowAny]

    def get(self, request, identifier):
        try:
            certificate = get_certificate_by_public_id(identifier)
        except Certificate.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Certificate not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(_verification_payload(certificate), status=status.HTTP_200_OK)


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
        filename = certificate.certificate_id or certificate.certificate_uid
        response['Content-Disposition'] = f'attachment; filename="certificate-{filename}.pdf"'
        return response


class AdminCertificateListView(APIView):
    """
    GET /api/v1/courses/admin/certificates/

    Platform-wide certificate browser for the admin console — the discovery
    surface for revoke/restore, which otherwise require a UUID the admin has no
    way to look up.

    Query params: ?search= (certificate ID / learner / course, >= 2 chars),
    ?status=valid|revoked, ?sort= (whitelist), plus the standard paginator's
    ?page / ?page_size.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]

    def get(self, request):
        queryset = search_certificates(request.query_params)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = AdminCertificateListSerializer(page, many=True)
        paginated_response = paginator.get_paginated_response(serializer.data)
        paginated_response.data = {'success': True, 'data': paginated_response.data}
        return paginated_response


class CertificateRevokeView(APIView):
    """
    POST /api/v1/courses/certificates/<uuid:certificate_uid>/revoke/

    Admin-only. Body: {"reason": "..."} (optional). Flips the verification
    verdict without touching the issued snapshot — the record of what was
    awarded stays intact. Already revoked → 422.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]

    def post(self, request, certificate_uid):
        reason = (request.data.get('reason') or '').strip()
        try:
            certificate = revoke_certificate(
                certificate_uid, actor=request.user, reason=reason,
            )
        except Certificate.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Certificate not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except CertificateError as e:
            return Response(
                {'success': False, 'message': e.message}, status=e.http_status,
            )
        except Exception:
            logger.exception('Certificate revoke failed for %s', certificate_uid)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                'success': True,
                'message': 'Certificate revoked.',
                'data': CertificateSerializer(certificate).data,
            },
            status=status.HTTP_200_OK,
        )


class CertificateRestoreView(APIView):
    """
    POST /api/v1/courses/certificates/<uuid:certificate_uid>/restore/

    Admin-only. Lifts a revocation. Not revoked → 422.
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]

    def post(self, request, certificate_uid):
        try:
            certificate = restore_certificate(certificate_uid, actor=request.user)
        except Certificate.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Certificate not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        except CertificateError as e:
            return Response(
                {'success': False, 'message': e.message}, status=e.http_status,
            )
        except Exception:
            logger.exception('Certificate restore failed for %s', certificate_uid)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {
                'success': True,
                'message': 'Certificate restored.',
                'data': CertificateSerializer(certificate).data,
            },
            status=status.HTTP_200_OK,
        )
