import logging

from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

logger = logging.getLogger(__name__)


def blacklist_all_refresh_tokens(user):
    """Blacklist every outstanding refresh token for the user.

    Used after a password change/reset or an admin suspension so existing
    refresh tokens cannot mint new access tokens. Idempotent and best-effort:
    a failure never rolls back the caller's primary action, but — because this
    is a security control — it is logged at CRITICAL so a silent fail-open is
    visible. Bulk-inserts and skips already-blacklisted tokens so the query
    count stays independent of how many times the user has refreshed.
    """
    try:
        already = BlacklistedToken.objects.filter(
            token__user=user
        ).values_list('token_id', flat=True)
        outstanding = OutstandingToken.objects.filter(user=user).exclude(id__in=already)
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token=token) for token in outstanding],
            ignore_conflicts=True,
        )
    except Exception:
        logger.critical(
            'Failed to blacklist refresh tokens for user %s; refresh tokens may '
            'remain valid after password change/suspension.',
            getattr(user, 'pk', user),
            exc_info=True,
        )
