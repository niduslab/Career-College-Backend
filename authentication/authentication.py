"""
Custom DRF authentication classes.

CookieJWTAuthentication lets protected endpoints read the JWT from the
HttpOnly ``access_token`` cookie (set by ``set_jwt_cookies``) in addition to
the ``Authorization: Bearer`` header. Browser clients rely on the cookie;
Postman/header clients still work via the built-in JWTAuthentication.
"""

from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication


class CookieJWTAuthentication(JWTAuthentication):
    """Authenticate using the JWT stored in the HttpOnly access-token cookie."""

    def authenticate(self, request):
        cookie_name = getattr(settings, 'JWT_ACCESS_COOKIE_NAME', 'access_token')
        raw_token = request.COOKIES.get(cookie_name)
        if raw_token is None:
            return None
        validated_token = self.get_validated_token(raw_token)
        return self.get_user(validated_token), validated_token
