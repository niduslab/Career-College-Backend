from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsEmailVerified, IsVerifiedPartnerInstitution
from webinars.models import Webinar
from webinars.serializers import WebinarSerializer
from webinars.services import WebinarError, assign_webinar_host, clear_webinar_host


class WebinarHostView(APIView):
    """
    Assign or clear the host expert on an institution-owned webinar.

    POST   {pk}/host/   body {expert_user_id}  → assign
    DELETE {pk}/host/                          → clear

    Institution-only, scoped to the owning institution (numeric pk → 404 on no-access).
    """

    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]

    def _get_webinar(self, request, pk):
        institution = request.user.partner_institution_profile
        return get_object_or_404(
            Webinar.objects.filter(partner_institution=institution),
            pk=pk,
        )

    def post(self, request, pk):
        webinar = self._get_webinar(request, pk)
        institution = request.user.partner_institution_profile

        expert_user_id = request.data.get('expert_user_id')
        if not expert_user_id:
            return Response(
                {'success': False, 'message': 'expert_user_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            assign_webinar_host(webinar, institution, expert_user_id)
        except WebinarError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )

        webinar.refresh_from_db()
        return Response(
            {
                'success': True,
                'message': 'Host expert assigned.',
                'data': WebinarSerializer(webinar).data,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        webinar = self._get_webinar(request, pk)
        institution = request.user.partner_institution_profile

        try:
            clear_webinar_host(webinar, institution)
        except WebinarError as exc:
            return Response(
                {'success': False, 'message': exc.message},
                status=exc.http_status,
            )

        return Response(
            {'success': True, 'message': 'Host expert cleared.'},
            status=status.HTTP_200_OK,
        )
