"""Project-wide DRF exception handler.

Views in this project return the standard envelope on error:

    {'success': False, 'message': '...', 'errors': {...}}

but exceptions *raised* by DRF itself never reach that code — throttling,
authentication, `permission_classes` denials, 405s and parse errors are
rendered by DRF as a bare ``{'detail': '...'}``. A client reading
``response.data.message`` therefore got ``undefined`` on exactly those
responses.

This handler rewraps them so every error the API emits carries `success` and
`message`. The original `detail` key is kept alongside the envelope, so
clients written against DRF's default shape keep working.

Unhandled exceptions are passed straight through (the handler returns None,
same as DRF's default) — the per-view try/except blocks own the 500 path.
"""

from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

FALLBACK_MESSAGE = 'An unexpected error occurred. Please try again.'


def _first_message(data) -> str:
    """Reduce a DRF error body to a single human-readable string."""
    if isinstance(data, dict):
        if data.get('detail') is not None:
            return _first_message(data['detail'])
        for value in data.values():
            return _first_message(value)
        return FALLBACK_MESSAGE
    if isinstance(data, (list, tuple)):
        return _first_message(data[0]) if data else FALLBACK_MESSAGE
    return str(data) if data not in (None, '') else FALLBACK_MESSAGE


def envelope_exception_handler(exc, context):
    """Wrap DRF-raised errors in the project's `success`/`message` envelope."""
    response = drf_exception_handler(exc, context)
    if response is None:
        # Not a DRF exception — leave Django's 500 path untouched.
        return None

    data = response.data

    # Field-level validation keeps its per-field map under `errors`, matching
    # what the views build by hand after `serializer.is_valid()`.
    if isinstance(exc, ValidationError):
        response.data = {
            'success': False,
            'message': 'Validation failed.',
            'errors': data,
        }
        return response

    payload = {'success': False, 'message': _first_message(data)}
    if isinstance(data, dict) and 'detail' in data:
        payload['detail'] = data['detail']
    response.data = payload
    return response
