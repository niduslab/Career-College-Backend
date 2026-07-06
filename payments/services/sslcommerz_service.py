"""Thin SSLCommerz gateway client — pure HTTP I/O, no business logic.

Two calls:
- `initiate_session` opens a hosted-checkout session (gwprocess v4) and
  returns the GatewayPageURL the browser is redirected to.
- `validate_transaction` re-queries the Validation API with a `val_id`.
  This is the ONLY trusted source of payment truth — redirect and IPN
  bodies are never believed on their own.

Base URL switches between sandbox and live via `SSLCOMMERZ_SANDBOX`.
"""

import hashlib
import logging

import requests
from django.conf import settings

from payments.services.exceptions import PaymentError

logger = logging.getLogger(__name__)

SESSION_ENDPOINT = '/gwprocess/v4/api.php'
VALIDATION_ENDPOINT = '/validator/api/validationserverAPI.php'
TRANSACTION_QUERY_ENDPOINT = '/validator/api/merchantTransIDvalidationAPI.php'
REQUEST_TIMEOUT = (5, 20)  # (connect, read) seconds

_GATEWAY_DOWN_MSG = 'Payment gateway is currently unavailable. Please try again.'


def _callback_url(name):
    return f"{settings.BACKEND_URL}/api/v1/payments/{name}/"


def initiate_session(order, user):
    """Open a gateway checkout session for `order`; return the parsed session
    response dict (contains `GatewayPageURL`).

    Raises PaymentError(503) on network failure, malformed response, or a
    non-SUCCESS gateway status.
    """
    payload = {
        'store_id': settings.SSLCOMMERZ_STORE_ID,
        'store_passwd': settings.SSLCOMMERZ_STORE_PASSWORD,
        'total_amount': str(order.amount),
        'currency': order.currency,
        'tran_id': order.tran_id,
        'success_url': _callback_url('success'),
        'fail_url': _callback_url('fail'),
        'cancel_url': _callback_url('cancel'),
        'ipn_url': _callback_url('ipn'),
        # Product — digital content, nothing shipped.
        'product_name': order.item.title[:255],
        'product_category': 'E-Learning',
        'product_profile': 'non-physical-goods',
        'shipping_method': 'NO',
        'num_of_item': 1,
        # Customer — gateway requires these fields even for digital goods.
        'cus_name': user.full_name or user.email,
        'cus_email': user.email,
        'cus_add1': 'N/A',
        'cus_city': 'N/A',
        'cus_country': 'Bangladesh',
        'cus_phone': 'N/A',
    }

    try:
        response = requests.post(
            f"{settings.SSLCOMMERZ_BASE_URL}{SESSION_ENDPOINT}",
            data=payload,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.exception('SSLCommerz session initiation failed: tran_id=%s', order.tran_id)
        raise PaymentError(_GATEWAY_DOWN_MSG, http_status=503)

    if data.get('status') != 'SUCCESS' or not data.get('GatewayPageURL'):
        logger.error(
            'SSLCommerz session rejected: tran_id=%s status=%s reason=%s',
            order.tran_id, data.get('status'), data.get('failedreason'),
        )
        raise PaymentError(_GATEWAY_DOWN_MSG, http_status=503)

    return data


def validate_transaction(val_id):
    """Query the Validation API for `val_id`; return the parsed JSON dict.

    Raises PaymentError(503) on network failure or a malformed body. Result
    interpretation (VALID/VALIDATED, amount matching) is the order service's
    job — this function only transports.
    """
    try:
        response = requests.get(
            f"{settings.SSLCOMMERZ_BASE_URL}{VALIDATION_ENDPOINT}",
            params={
                'val_id': val_id,
                'store_id': settings.SSLCOMMERZ_STORE_ID,
                'store_passwd': settings.SSLCOMMERZ_STORE_PASSWORD,
                'format': 'json',
                'v': 1,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        logger.exception('SSLCommerz validation call failed: val_id=%s', val_id)
        raise PaymentError(_GATEWAY_DOWN_MSG, http_status=503)


def query_transaction(tran_id):
    """Look up a transaction by OUR tran_id (reconciliation path for the reaper).

    Returns the parsed JSON: `{APIConnect, no_of_trans_found, element: [...]}`.
    Raises PaymentError(503) on network/parse failure. Interpretation of the
    returned status is the caller's job.
    """
    try:
        response = requests.get(
            f"{settings.SSLCOMMERZ_BASE_URL}{TRANSACTION_QUERY_ENDPOINT}",
            params={
                'tran_id': tran_id,
                'store_id': settings.SSLCOMMERZ_STORE_ID,
                'store_passwd': settings.SSLCOMMERZ_STORE_PASSWORD,
                'format': 'json',
                'v': 1,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        logger.exception('SSLCommerz transaction query failed: tran_id=%s', tran_id)
        raise PaymentError(_GATEWAY_DOWN_MSG, http_status=503)


def verify_callback_signature(data):
    """Verify an SSLCommerz callback/IPN hash (`verify_sign` + `verify_key`).

    Returns True only when both hash fields are present AND the recomputed MD5
    matches. Missing fields → False (the caller decides whether that's fatal).
    This is the authenticity gate for the fail/cancel callbacks, which — unlike
    the success path — do not re-hit the validation API.

    Algorithm (per SSLCommerz spec): collect the fields named in `verify_key`,
    add `store_passwd = md5(store_password)`, sort by key, join `k=v` with `&`,
    MD5 the result, compare to `verify_sign`.
    """
    verify_sign = data.get('verify_sign')
    verify_key = data.get('verify_key')
    if not verify_sign or not verify_key:
        return False

    pairs = {k: data.get(k, '') for k in verify_key.split(',') if k in data}
    pairs['store_passwd'] = hashlib.md5(
        settings.SSLCOMMERZ_STORE_PASSWORD.encode()
    ).hexdigest()
    hash_string = '&'.join(f'{k}={pairs[k]}' for k in sorted(pairs))
    computed = hashlib.md5(hash_string.encode()).hexdigest()
    return computed == verify_sign
