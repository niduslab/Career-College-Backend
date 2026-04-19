"""Custom exceptions for the Google OAuth authorization-code flow."""


class GoogleOAuthError(Exception):
    """Base error for all Google OAuth failures."""


class GoogleOAuthConfigError(GoogleOAuthError):
    """Google OAuth is misconfigured on the server side."""


class GoogleOAuthCodeExchangeError(GoogleOAuthError):
    """Failed to exchange the authorization code with Google."""


class GoogleOAuthProfileError(GoogleOAuthError):
    """Failed to fetch the user profile from Google."""


class GoogleOAuthAccountConflictError(GoogleOAuthError):
    """SocialAccount linking conflict (sub mismatch or duplicate link)."""


class GoogleOAuthBlockedUserError(GoogleOAuthError):
    """The matched user is deleted, inactive, restricted, or has a blocked role."""
