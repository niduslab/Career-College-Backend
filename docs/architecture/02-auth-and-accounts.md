# 02) Auth And Accounts

## Key files

| File | Purpose |
|------|---------|
| `authentication/models.py` | `User` model, profile models, OTP/reset methods |
| `authentication/signals.py` | Auto-create profile on user creation |
| `authentication/all_views/auth_views.py` | Register, login, logout, token refresh |
| `authentication/all_views/otp_views.py` | OTP verify/resend |
| `authentication/all_views/password_views.py` | Forgot/reset/change password |
| `authentication/all_views/google_views.py` | Google OAuth redirect/callback/exchange |
| `authentication/all_views/linkedin_views.py` | LinkedIn OAuth redirect/callback/exchange |
| `authentication/services/google_oauth.py` | Google token exchange and user provisioning |
| `authentication/services/linkedin_oauth.py` | LinkedIn token exchange and user provisioning |
| `authentication/serializers.py` | Request validation and response shaping |

## Main model: `User`

Custom `AbstractUser` that replaces Django's default. Email is the unique identifier — there is no
`username` field.

**Identity:**
- `email` — unique login identifier, indexed
- `full_name` — required on registration (min 2 chars)
- `name_slug` — auto-generated URL-safe public identifier

**Role:**
- `user_type` — `learner | instructor | partner_institution | admin`

**Account status flags:**
- `is_email_verified` — set to `True` after OTP verification; gates most endpoints
- `is_verified` — platform-level verification; auto-`True` for learners, requires admin/identity-check for instructors
- `is_active` — account active (cannot be self-set to `False`)
- `is_restricted_by_admin` — admin-revoked access; user cannot self-reactivate
- `is_deleted` — soft delete; `deleted_at` and `deletion_reason` stored; email mangled to `original@.deleted.{id}` so the address can be re-registered

**OTP fields** (stored on `User`, not a separate table):
- `otp_code` — 6-digit numeric code
- `otp_created_at` — set when OTP is generated; valid for **2 minutes**
- `otp_purpose` — `registration | password_reset`
- `otp_verified` — set `True` after successful OTP verification (used only in password-reset flow)

**Password reset fields:**
- `password_reset_token` — 64-character URL-safe token; valid for **15 minutes**
- `password_reset_token_created_at`

**Key model methods:**
- `generate_otp(purpose)` — creates 6-digit OTP, stamps `otp_created_at`, saves
- `verify_otp(otp, purpose, clear_otp=True)` — validates code + 2-min window; optionally clears fields
- `generate_password_reset_token()` — creates 64-char token, stamps expiry
- `verify_password_reset_token(token)` — validates match + 15-min window
- `update_password(new_password)` — sets password, clears all reset/OTP fields
- `soft_delete(reason)` — sets `is_deleted=True`, mangles email

---

## Registration and email verification flow

```
POST /api/v1/auth/register/
         │
         ▼
UserRegistrationSerializer validates:
  • email uniqueness + format
  • full_name ≥ 2 chars
  • password strength (Django validator + custom strength check)
  • password == confirm_password
  • partner_institution: institution_name + type required;
    institutional email required (blocks gmail/yahoo/etc)
         │
         ▼
User.objects.create_user(email, password, ...)
         │
         ▼
authentication/signals.py: post_save signal
  → auto-creates matching profile row:
    learner          → LearnerProfile
    instructor       → InstructorProfile
    partner_institution → PartnerInstitutionProfile
         │
         ▼
user.generate_otp(purpose='registration')
         │
         ▼
Email sent with OTP (console backend in dev)
         │
         ▼
201 Created

─────────────────────────────────────────────

POST /api/v1/auth/otp/verify/
  body: { email, otp, purpose: "registration" }
         │
         ▼
user.verify_otp(otp, 'registration')
  • matches otp_code
  • checks otp_created_at + 2-minute window
  • clears otp fields
         │
         ▼
user.is_email_verified = True
         │
         ▼
200 OK — user can now log in
```

**OTP rate limiting:**
- 20 requests/minute per IP (throttle class)
- 30-second debounce per (user, purpose) — prevents email spam

---

## Login and JWT token flow

```
POST /api/v1/auth/login/
  body: { email, password }
         │
         ▼
Validates credentials:
  • user exists and is_active
  • password correct
  • is_email_verified (403 if not)
  • is_restricted_by_admin (403 if restricted)
         │
         ▼
SimpleJWT generates:
  • access token  — 12-hour lifetime
  • refresh token — 7-day lifetime, rotation enabled,
                    blacklisted on use (old token invalid after rotate)
         │
         ▼
Response body: { access, refresh, user: {...} }
Optionally: HttpOnly cookies set via cookie_helpers.py
            (JWT_COOKIE_SECURE=False for local HTTP dev)
```

**Token refresh:**
```
POST /api/v1/auth/token/refresh/
  body: { refresh }
  → returns new access token + rotated refresh token
  → old refresh token blacklisted
```

---

## Password reset flow

```
Step 1 — Initiate reset
POST /api/v1/auth/password/forgot/
  body: { email }
         │
         ▼
Validates:
  • account exists and is active
  • email is verified
  • not restricted by admin
  • 30-second debounce (prevents rapid re-sends)
         │
         ▼
user.generate_otp(purpose='password_reset')
Email sent with 6-digit OTP (2-minute window)

─────────────────────────────────────────────

Step 2 — Verify OTP
POST /api/v1/auth/otp/verify/
  body: { email, otp, purpose: "password_reset" }
         │
         ▼
user.verify_otp(otp, 'password_reset', clear_otp=True)
         │
         ▼
user.otp_verified = True
user.generate_password_reset_token()
  → 64-char URL-safe token stored on user
  → valid 15 minutes from generation
         │
         ▼
200 OK — response includes { reset_token }

─────────────────────────────────────────────

Step 3 — Apply new password
POST /api/v1/auth/password/reset/
  body: { email, reset_token, new_password, confirm_password }
         │
         ▼
Validates:
  • token matches + not expired (15-min window)
  • new_password strength
  • new_password == confirm_password
         │
         ▼
user.update_password(new_password)
  → set_password(), clear reset_token + OTP fields, save
         │
         ▼
200 OK — user can log in with new password
```

---

## Change password (authenticated)

```
POST /api/v1/auth/password/change/
  body: { current_password, new_password, confirm_password }
  Permission: IsAuthenticated + IsEmailVerified
         │
         ▼
Validates: current_password correct, new != current, passwords match
user.set_password(new_password)
200 OK
```

---

## OAuth flow (Google and LinkedIn)

Both providers follow the same pattern. Details below use Google as example.

```
Step 1 — Redirect to provider
GET /api/v1/auth/google/?user_type=<learner|instructor|...>
         │
         ▼
Generates Google authorization URL
Stores user_type in session (defaults to 'learner')
302 → Google consent screen

─────────────────────────────────────────────

Step 2 — Provider callback
GET /api/v1/auth/google/callback/?code=<code>&state=<state>
         │
         ▼
If FRONTEND_GOOGLE_CALLBACK configured:
  • returns code to frontend (redirect)
  • frontend POSTs code to exchange endpoint

If no frontend URL:
  • exchanges code with Google directly
  • provisions user
  • sets JWT + HttpOnly cookies
  • returns user JSON

─────────────────────────────────────────────

Step 3 — Token exchange (frontend-driven flow)
POST /api/v1/auth/google/exchange-token/
  body: { code, user_type }
         │
         ▼
exchange_code_for_tokens(code)       ← Google API call
         │
         ▼
fetch_google_profile(access_token)   ← Google API call
  returns: email, name, picture
         │
         ▼
get_or_create_google_user(profile, user_type)
  • if email exists → link social account, return existing user
  • if new → create User + trigger profile signal
  → returns (user, is_new_user)
         │
         ▼
sync_social_account()
  • prevents duplicate email linking across providers
  • raises GoogleOAuthAccountConflictError (409) if conflict
         │
         ▼
generate_jwt_tokens(user)
Set HttpOnly cookies
         │
         ▼
200 OK — { user_id, email, full_name, user_type,
            is_email_verified, is_verified, profile_photo_url }
```

**Google OAuth error types:**

| Error | HTTP | Cause |
|-------|------|-------|
| `GoogleOAuthConfigError` | 503 | Missing client ID/secret in env |
| `GoogleOAuthCodeExchangeError` | 400 | Invalid or expired authorization code |
| `GoogleOAuthProfileError` | 400 | Failed to fetch user profile from Google |
| `GoogleOAuthBlockedUserError` | 403 | User account is restricted |
| `GoogleOAuthAccountConflictError` | 409 | Email already linked to different provider |

LinkedIn follows the identical pattern; error types are `LinkedInOAuth*` equivalents.

---

## Summary: account lifecycle

```
Register ──► Verify Email (OTP) ──► Login (JWT)
                                         │
                                    ┌────┴────┐
                                    │         │
                               Access API  Refresh Token
                                    │         │
                                    │    (rotates every use,
                                    │     old token blacklisted)
                                    │
                               Logout (blacklist refresh)
```

---

## Why this design

- **OTP + email verification** reduces fake account abuse before any marketplace action is taken.
- **Separate OTP fields on `User`** (not a separate table) keeps security state centralized without
  an extra JOIN on every auth check.
- **Reset token on `User`** with 15-minute expiry prevents indefinite-validity password-reset links.
- **Rotating refresh tokens with blacklist** limit the exposure window if a refresh token is stolen —
  the old token is invalid immediately after the next refresh.
- **OAuth services isolated from views** (`authentication/services/`) allow testing provider
  integration independently of HTTP handling.
- **Soft-delete with email mangling** allows re-registration of the same address without violating
  the unique constraint.
