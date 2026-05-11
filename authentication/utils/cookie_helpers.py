"""
Helper for setting HttpOnly JWT cookies on responses.

All cookie parameters are driven by Django settings so they can be
tuned per-environment (dev vs. production).
"""

from django.conf import settings


def _cookie_settings():
    """Return a dict of cookie kwargs from Django settings."""
    return {
        'httponly': True,
        'secure': getattr(settings, 'JWT_COOKIE_SECURE', not settings.DEBUG),
        'samesite': getattr(settings, 'JWT_COOKIE_SAMESITE', 'Lax'),
        'domain': getattr(settings, 'JWT_COOKIE_DOMAIN', None),
        'path': getattr(settings, 'JWT_COOKIE_PATH', '/'),
    }


def set_jwt_cookies(response, tokens):
    """
    Set access and refresh JWT tokens as HttpOnly cookies on *response*.

    Cookie names default to 'access_token' and 'refresh_token' but can be
    overridden via JWT_ACCESS_COOKIE_NAME / JWT_REFRESH_COOKIE_NAME.
    """
    base = _cookie_settings()

    access_name = getattr(settings, 'JWT_ACCESS_COOKIE_NAME', 'access_token')
    refresh_name = getattr(settings, 'JWT_REFRESH_COOKIE_NAME', 'refresh_token')

    access_max_age = int(
        settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()
    )
    refresh_max_age = int(
        settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()
    )

    response.set_cookie(
        key=access_name,
        value=tokens['access'],
        max_age=access_max_age,
        **base,
    )
    response.set_cookie(
        key=refresh_name,
        value=tokens['refresh'],
        max_age=refresh_max_age,
        **base,
    )
    return response


def delete_jwt_cookies(response):
    """Remove JWT cookies (useful on logout)."""
    access_name = getattr(settings, 'JWT_ACCESS_COOKIE_NAME', 'access_token')
    refresh_name = getattr(settings, 'JWT_REFRESH_COOKIE_NAME', 'refresh_token')
    base = _cookie_settings()

    response.delete_cookie(access_name, path=base['path'], domain=base['domain'])
    response.delete_cookie(refresh_name, path=base['path'], domain=base['domain'])
    return response
