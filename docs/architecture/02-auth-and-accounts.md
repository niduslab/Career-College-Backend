# 02) Auth And Accounts

## Key files

- `auth/models.py`: user and account-related profile models
- `auth/urls.py`: auth endpoint paths
- `auth/views.py`: export layer
- `auth/all_views/auth_views.py`: register/login/logout/token refresh
- `auth/all_views/otp_views.py`: OTP verify/resend
- `auth/all_views/password_views.py`: forgot/reset/change password
- `auth/all_views/google_views.py`: Google OAuth flow
- `auth/all_views/linkedin_views.py`: LinkedIn OAuth flow
- `auth/services/*`: OAuth integrations and user provisioning logic
- `auth/serializers.py`: request validation/serialization
- `auth/signals.py`: post-save hooks (if any)

## Main model: `User`

From `auth/models.py`:

- Identity:
  - `email` (unique login id)
  - `full_name`
  - `name_slug` (public profile slug)
- Account type:
  - `user_type`: `learner|instructor|partner_institution|admin`
- Status flags:
  - `is_email_verified`
  - `is_verified` (platform-level verification state)
  - `is_active`
  - `is_restricted_by_admin`
  - `is_deleted` (soft-delete)
- Soft-delete metadata:
  - `deleted_at`
  - `deletion_reason`
- OTP and reset:
  - `otp_code`, `otp_created_at`, `otp_purpose`, `otp_verified`
  - `password_reset_token`, `password_reset_token_created_at`

## Account lifecycle process

1. User registers (`/auth/register/`).
2. OTP is generated and sent.
3. User verifies OTP (`/auth/otp/verify/`) -> email verified state.
4. User logs in (`/auth/login/`) -> JWT/cookie flow.
5. Refresh token (`/auth/token/refresh/`) keeps session alive.
6. Logout (`/auth/logout/`) clears session/token state.

## Password reset process

1. `/auth/password/forgot/` starts OTP reset process.
2. `/auth/otp/verify/` with reset purpose validates ownership.
3. Server creates `password_reset_token`.
4. `/auth/password/reset/` applies new password using that token.
5. Password reset fields are cleared.

## OAuth process

Google and LinkedIn endpoints support auth-code + exchange style flow:

- Redirect endpoint -> provider consent
- Callback endpoint -> code received
- Exchange endpoint -> token/user profile exchange -> local user provisioning

See:
- `auth/all_views/google_views.py`
- `auth/all_views/linkedin_views.py`
- `auth/services/google_oauth.py`
- `auth/services/linkedin_oauth.py`

## Workflow

1. User identity is created in `User`.
2. Verification state is established through OTP or social login.
3. Session/token access is issued for authenticated operations.
4. Password and recovery flows update secure credential state.
5. Permission checks gate access to protected APIs.

## System Explanation (Why This Design)

- OTP + email verification reduces abuse and fake accounts.
- Social login services are isolated from API views for cleaner integration.
- Keeping reset/OTP metadata on `User` centralizes security state.
