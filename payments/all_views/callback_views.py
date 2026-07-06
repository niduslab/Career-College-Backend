"""SSLCommerz gateway callbacks.

All views here are unauthenticated (`authentication_classes = []`,
`AllowAny`): the gateway POSTs without our JWT, and the browser redirects
carry no auth context either. Authenticity is established server-side by
`finalize_payment` re-querying the SSLCommerz Validation API with the
`val_id` — the request body itself is never trusted for payment state.

The success redirect is the primary finalize path (local sandbox has no
public IPN URL); the IPN is the server-to-server safety net. Both funnel
into the idempotent `finalize_payment`, so double-fire is harmless.
"""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from payments.services import (
    PaymentError,
    finalize_payment,
    mark_order_cancelled,
    mark_order_failed,
)
from payments.services.sslcommerz_service import verify_callback_signature

logger = logging.getLogger(__name__)

_IPN_OK = {'success': True, 'message': 'IPN received.'}


def _frontend_redirect(path, **params):
    url = f"{settings.FRONTEND_URL}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    return HttpResponseRedirect(url)


def _callback_params(request):
    """tran_id/val_id/status from POST body (gateway) or query string (manual retry)."""
    source = request.data if request.data else request.query_params
    return (
        source.get('tran_id', ''),
        source.get('val_id', ''),
        source.get('status', ''),
    )


class PaymentIPNView(APIView):
    """POST /api/v1/payments/ipn/ — SSLCommerz server-to-server notification."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        tran_id, val_id, gw_status = _callback_params(request)
        if not tran_id:
            logger.warning('IPN without tran_id ignored.')
            return Response(_IPN_OK, status=status.HTTP_200_OK)

        try:
            if gw_status in ('VALID', 'VALIDATED') and val_id:
                # Authenticity comes from re-hitting the validation API inside
                # finalize_payment — the body is not trusted for the money path.
                finalize_payment(tran_id, val_id)
            elif gw_status in ('FAILED', 'EXPIRED', 'CANCELLED'):
                # These paths trust the body's status, so require a valid
                # signature — otherwise anyone who learns a tran_id could force
                # an in-flight order to failed/cancelled.
                if not verify_callback_signature(request.data):
                    logger.warning('Unsigned/invalid %s IPN ignored: tran_id=%s', gw_status, tran_id)
                    return Response(_IPN_OK, status=status.HTTP_200_OK)
                if gw_status == 'CANCELLED':
                    mark_order_cancelled(tran_id, payload=dict(request.data))
                else:
                    mark_order_failed(tran_id, payload=dict(request.data))
            else:
                logger.warning('IPN with unhandled status %r: tran_id=%s', gw_status, tran_id)
        except PaymentError as exc:
            if exc.http_status >= 500:
                # Transient (gateway unreachable during validation) — 500 so
                # SSLCommerz retries the IPN rather than leaving the order stranded.
                logger.error('IPN validation transient failure; signaling retry: tran_id=%s — %s',
                             tran_id, exc.message)
                return Response(
                    {'success': False, 'message': 'Temporary failure. Please retry.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            # Permanent condition (validation rejected / unknown order) —
            # acknowledge so the gateway stops retrying.
            logger.warning('IPN finalize rejected: tran_id=%s — %s', tran_id, exc.message)
        except Exception:
            # Unexpected failure — 500 so SSLCommerz retries the IPN.
            logger.exception('IPN processing failed: tran_id=%s', tran_id)
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(_IPN_OK, status=status.HTTP_200_OK)


class PaymentSuccessView(APIView):
    """Gateway redirects the browser here after a successful payment."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        return self._handle(request)

    def get(self, request):
        return self._handle(request)

    def _handle(self, request):
        tran_id, val_id, _ = _callback_params(request)
        if not tran_id or not val_id:
            logger.warning('Success callback missing tran_id/val_id.')
            return _frontend_redirect(
                settings.FRONTEND_PAYMENT_FAIL_PATH, reason='missing_params',
            )
        try:
            finalize_payment(tran_id, val_id)
        except PaymentError as exc:
            logger.warning('Success-redirect finalize rejected: tran_id=%s — %s', tran_id, exc.message)
            return _frontend_redirect(
                settings.FRONTEND_PAYMENT_FAIL_PATH,
                tran_id=tran_id, reason='validation_failed',
            )
        except Exception:
            logger.exception('Success-redirect finalize crashed: tran_id=%s', tran_id)
            return _frontend_redirect(
                settings.FRONTEND_PAYMENT_FAIL_PATH,
                tran_id=tran_id, reason='error',
            )
        return _frontend_redirect(settings.FRONTEND_PAYMENT_SUCCESS_PATH, tran_id=tran_id)


class _TerminalRedirectView(APIView):
    """Shared base for the fail/cancel browser redirects. Marks the order only
    when the callback signature is valid (the body is otherwise untrusted), then
    redirects to the configured frontend path regardless. An unsigned callback
    leaves the order in `processing` for the reconciliation reaper to resolve."""

    authentication_classes = []
    permission_classes = [AllowAny]

    frontend_path = None   # settings attribute name
    mark = None            # staticmethod(mark_order_failed / mark_order_cancelled)
    label = ''

    def post(self, request):
        return self._handle(request)

    def get(self, request):
        return self._handle(request)

    def _handle(self, request):
        tran_id, _, _ = _callback_params(request)
        if tran_id:
            if verify_callback_signature(request.data):
                type(self).mark(tran_id, payload=dict(request.data))
            else:
                logger.warning('Unsigned/invalid %s callback ignored: tran_id=%s', self.label, tran_id)
        return _frontend_redirect(getattr(settings, self.frontend_path), tran_id=tran_id)


class PaymentFailView(_TerminalRedirectView):
    """Gateway redirects the browser here after a failed payment."""

    frontend_path = 'FRONTEND_PAYMENT_FAIL_PATH'
    mark = staticmethod(mark_order_failed)
    label = 'fail'


class PaymentCancelView(_TerminalRedirectView):
    """Gateway redirects the browser here when the user cancels at checkout."""

    frontend_path = 'FRONTEND_PAYMENT_CANCEL_PATH'
    mark = staticmethod(mark_order_cancelled)
    label = 'cancel'
