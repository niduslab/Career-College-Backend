from datetime import datetime

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsEmailVerified, IsPlatformAdmin
from payouts.all_models.payout_models import Payout, PayoutAccount
from payouts.all_serializers.payout_serializers import (
    PayoutAccountSerializer,
    PayoutAccountWriteSerializer,
    PayoutSerializer,
)
from payouts.services.payout_service import (
    PayoutError,
    generate_payouts,
    mark_payout_paid,
    review_payout,
    search_payout_accounts,
    search_payouts,
    verify_payout_account,
)


def _paginated(request, queryset, serializer_class):
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = serializer_class(page, many=True)
    paginated_response = paginator.get_paginated_response(serializer.data)
    paginated_response.data = {'success': True, 'data': paginated_response.data}
    return paginated_response


def _parse_date(value, field_name):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise PayoutError(f'{field_name} must be a valid date (YYYY-MM-DD).', 400)


class MyPayoutAccountView(APIView):
    """GET/PATCH — instructor or partner-institution manages their own payout account."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def _get_or_none(self, request):
        if request.user.user_type == 'instructor':
            return PayoutAccount.objects.filter(instructor=request.user).first()
        if request.user.user_type == 'partner_institution':
            return PayoutAccount.objects.filter(
                institution=request.user.partner_institution_profile,
            ).first()
        return None

    def _assert_eligible(self, request):
        if request.user.user_type not in ('instructor', 'partner_institution'):
            return Response(
                {'success': False, 'message': 'Only instructors and partner institutions have a payout account.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def get(self, request):
        if err := self._assert_eligible(request):
            return err
        account = self._get_or_none(request)
        if not account:
            return Response({'success': True, 'data': None}, status=status.HTTP_200_OK)
        return Response(
            {'success': True, 'data': PayoutAccountSerializer(account).data},
            status=status.HTTP_200_OK,
        )

    def patch(self, request):
        if err := self._assert_eligible(request):
            return err
        account = self._get_or_none(request)
        serializer = PayoutAccountWriteSerializer(
            instance=account, data=request.data, partial=True,
        )
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        extra = {}
        if account is None:
            if request.user.user_type == 'instructor':
                extra['instructor'] = request.user
            else:
                extra['institution'] = request.user.partner_institution_profile

        saved = serializer.save(**extra)
        return Response(
            {'success': True, 'message': 'Payout account saved.', 'data': PayoutAccountSerializer(saved).data},
            status=status.HTTP_200_OK,
        )


class MyPayoutListView(APIView):
    """GET — instructor or partner-institution views their own payout history."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        if request.user.user_type == 'instructor':
            qs = Payout.objects.filter(payout_account__instructor=request.user)
        elif request.user.user_type == 'partner_institution':
            qs = Payout.objects.filter(
                payout_account__institution=request.user.partner_institution_profile,
            )
        else:
            return Response(
                {'success': False, 'message': 'Only instructors and partner institutions have payouts.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = qs.select_related('payout_account').order_by('-requested_at')
        return _paginated(request, qs, PayoutSerializer)


_ADMIN_PERMS = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]


class AdminPayoutAccountListView(APIView):
    """GET /admin/payout-accounts/ — browse all payout accounts. ?is_verified= filter."""

    permission_classes = _ADMIN_PERMS

    def get(self, request):
        qs = search_payout_accounts(request.query_params)
        return _paginated(request, qs, PayoutAccountSerializer)


class AdminPayoutAccountVerifyView(APIView):
    """POST /admin/payout-accounts/<pk>/verify/"""

    permission_classes = _ADMIN_PERMS

    def post(self, request, pk):
        try:
            account = verify_payout_account(request.user, pk)
        except PayoutError as exc:
            return Response({'success': False, 'message': exc.message}, status=exc.http_status)
        return Response(
            {'success': True, 'message': 'Payout account verified.', 'data': PayoutAccountSerializer(account).data},
            status=status.HTTP_200_OK,
        )


class AdminPayoutGenerateView(APIView):
    """POST /admin/payouts/generate/ — body {period_start, period_end} (YYYY-MM-DD)."""

    permission_classes = _ADMIN_PERMS

    def post(self, request):
        try:
            period_start = _parse_date(request.data.get('period_start'), 'period_start')
            period_end = _parse_date(request.data.get('period_end'), 'period_end')
            created = generate_payouts(request.user, period_start, period_end)
        except PayoutError as exc:
            return Response({'success': False, 'message': exc.message}, status=exc.http_status)
        return Response(
            {
                'success': True,
                'message': f'{len(created)} payout(s) generated.',
                'data': PayoutSerializer(created, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AdminPayoutListView(APIView):
    """GET /admin/payouts/ — ?status= / ?search= filters."""

    permission_classes = _ADMIN_PERMS

    def get(self, request):
        try:
            qs = search_payouts(request.query_params)
        except PayoutError as exc:
            return Response({'success': False, 'message': exc.message}, status=exc.http_status)
        return _paginated(request, qs, PayoutSerializer)


class AdminPayoutDetailView(APIView):
    """GET /admin/payouts/<pk>/"""

    permission_classes = _ADMIN_PERMS

    def get(self, request, pk):
        try:
            payout = Payout.objects.select_related('payout_account').get(pk=pk)
        except Payout.DoesNotExist:
            return Response({'success': False, 'message': 'Payout not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'success': True, 'data': PayoutSerializer(payout).data}, status=status.HTTP_200_OK)


class AdminPayoutReviewView(APIView):
    """POST /admin/payouts/<pk>/review/ — body {action: approve|reject, rejection_reason?}."""

    permission_classes = _ADMIN_PERMS

    def post(self, request, pk):
        action = (request.data.get('action') or '').strip().lower()
        rejection_reason = request.data.get('rejection_reason', '')
        try:
            payout = review_payout(request.user, pk, action, rejection_reason)
        except PayoutError as exc:
            return Response({'success': False, 'message': exc.message}, status=exc.http_status)
        message = 'Payout approved.' if action == 'approve' else 'Payout rejected.'
        return Response(
            {'success': True, 'message': message, 'data': PayoutSerializer(payout).data},
            status=status.HTTP_200_OK,
        )


class AdminPayoutMarkPaidView(APIView):
    """POST /admin/payouts/<pk>/mark-paid/ — body {payment_reference}."""

    permission_classes = _ADMIN_PERMS

    def post(self, request, pk):
        payment_reference = (request.data.get('payment_reference') or '').strip()
        try:
            payout = mark_payout_paid(request.user, pk, payment_reference)
        except PayoutError as exc:
            return Response({'success': False, 'message': exc.message}, status=exc.http_status)
        return Response(
            {'success': True, 'message': 'Payout marked as paid.', 'data': PayoutSerializer(payout).data},
            status=status.HTTP_200_OK,
        )
