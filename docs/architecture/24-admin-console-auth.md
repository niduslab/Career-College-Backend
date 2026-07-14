# 24. Admin Console — Session Authentication

**App:** `admin_console/` · **Prefix:** `/api/v1/admin-console/` · **Status:** implemented (Sprint 8, item 1)

The `admin_console` app is the home for the platform back-office. Its first slice is a **session-based login** for admins, distinct from the JWT flow the rest of the platform uses. Everything else in the admin console (user management, moderation, financial admin — see `docs/future_implementations/ADMIN_CONSOLE.md`) will build on the base view established here.

## Why sessions, not JWT

The public API authenticates with a 12-hour JWT (HttpOnly cookie or `Bearer` header). That is a poor fit for a browser back-office that wants short idle timeouts, CSRF-protected writes, and "log out everywhere". Django sessions give all of that for free (`django_session` table, already migrated). Admin **auth** needs no model of its own; the app's one model, `AdminSession`, exists only for device-tracking + remote logout (see below).

## The core rule: session auth is per-view, never global

DRF's `SessionAuthentication` enforces CSRF on unsafe methods **only when it authenticates a user**. If it were added to the global `DEFAULT_AUTHENTICATION_CLASSES`, every existing JWT `POST`/`PATCH` across the platform would suddenly need a CSRF token — a breaking change. Instead it is enabled **only** on the admin-console base view:

```python
# admin_console/all_views/base.py
class AdminConsoleAPIView(APIView):
    authentication_classes = [SessionAuthentication, CookieJWTAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]
```

Session-primary with a JWT fallback: browser clients use the session cookie (CSRF enforced); automated tooling can still present a token (a JWT client has no session, so `SessionAuthentication` returns `None` and DRF falls through to JWT — no CSRF needed). **Every future admin-console endpoint subclasses `AdminConsoleAPIView`.**

## Endpoints

| Method + path | View | Auth | Purpose |
|---|---|---|---|
| `GET auth/csrf/` | `CsrfTokenView` | `AllowAny`, no auth classes | Primes the `csrftoken` cookie so the SPA can send `X-CSRFToken`. |
| `POST auth/login/` | `AdminLoginView` | `AllowAny`, no auth classes, throttled | Validates credentials + admin gate → `django.contrib.auth.login()` (rotates session key). Stores `session['admin_login_at']`. |
| `POST auth/logout/` | `AdminLogoutView` | base (session/JWT) | `django.contrib.auth.logout()` — flushes the session. CSRF-protected. |
| `GET auth/session/` | `AdminSessionView` | base (session/JWT) | Who-am-I / liveness check for the SPA; returns the admin profile + `idle_timeout_seconds`. |

## Shared login also opens an admin session

The common login `POST /api/v1/auth/login/` (`UserLoginView`) stays the single login for **every** role and still returns JWT. When the authenticated user is an admin (`is_staff or user_type == 'admin'`), it *additionally* calls `django.contrib.auth.login()`, stamps `session['admin_login_at']`, and primes the `csrftoken` cookie via `get_token()` — so one login call yields JWT **plus** `sessionid` + `csrftoken`, and the admin back-office works with no separate admin-console login step. Non-admins are unaffected (JWT only). `UserLoginView` is `AllowAny` with no `SessionAuthentication`, so opening the session there does not force CSRF on the login POST itself.

The dedicated `POST auth/login/` + `GET auth/csrf/` admin-console views below remain (harmless, still functional) but are no longer the only way to establish an admin session.

## Login validation & the 403-vs-400 split

`AdminLoginSerializer` mirrors `UserLoginSerializer.validate` (same account-state checks and messages: soft-deleted, inactive/restricted, unverified email → generic errors, no enumeration). The **admin-role gate** (`is_staff or user_type == 'admin'`) runs in the view, not the serializer, so the codes differ deliberately:

- **Bad/unknown credentials** → `400` generic ("Invalid email or password.") — no account enumeration.
- **Valid credentials but not an admin** → `403` ("Only administrators can sign in here.").

## Idle timeout & re-authentication

- **Idle timeout** — `SESSION_SAVE_EVERY_REQUEST = True` + `SESSION_COOKIE_AGE = ADMIN_SESSION_IDLE_TIMEOUT` (default 1800 s) → the session expires N seconds after the *last* request (sliding). `SESSION_EXPIRE_AT_BROWSER_CLOSE = True` closes it on browser exit.
- **Re-auth for sensitive actions** — `IsRecentlyAuthenticatedAdmin` (`core/permissions.py`) extends the admin gate with a freshness check: `now - session['admin_login_at']` must be ≤ `ADMIN_REAUTH_MAX_AGE` (default 900 s). The mechanism ships now; wire it onto sensitive endpoints as they are built (a JWT-only admin has no timestamp → asked to re-authenticate).

## Cookie / CSRF hardening (settings)

`SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SECURE = not DEBUG`, `SESSION_COOKIE_SAMESITE = 'Lax'`; `CSRF_COOKIE_SECURE = not DEBUG`, `CSRF_COOKIE_SAMESITE = 'Lax'`, `CSRF_COOKIE_HTTPONLY = False` (JS must read the token), `CSRF_TRUSTED_ORIGINS = [FRONTEND_URL]`. All env-overridable. Login is rate-limited by `AdminLoginThrottle` (scope `admin_login`, `ADMIN_LOGIN_RATE_LIMIT`, default `10/min`), mirroring the JWT `LoginThrottle`.

## Device/session tracking & remote logout

The app owns one model, `AdminSession` (`admin_console/all_models/session_models.py`), so an admin can see which devices they're signed in from and log any of them out. Django's `django_session` table stores only `session_key` / `session_data` / `expire_date` — no IP or device — so `AdminSession` keeps that extra metadata alongside it, linked by `session_key`:

| Field | Source |
|---|---|
| `user` | the logged-in admin (FK) |
| `session_key` | the `django_session` row it mirrors (unique) |
| `ip_address` | first `X-Forwarded-For` hop if present, else `REMOTE_ADDR` (proxy-dependent) |
| `user_agent` | raw UA string |
| `browser` / `os` / `device` | parsed from the UA by the `user-agents` lib at create time |
| `created_at` / `last_seen_at` | first seen / last activity |

**Capture** is centralized in a `user_logged_in` signal receiver (`admin_console/signals.py`, wired from `AdminConsoleConfig.ready()`), so both login paths (`UserLoginView`, `AdminLoginView`) and even Django `/admin/` feed it. The receiver no-ops for non-admins and for JWT-only clients (no `django_login` → no `session_key`), and `update_or_create`s per key (idempotent). A `user_logged_out` receiver deletes the row on explicit logout; `AdminConsoleAPIView.initial()` best-effort-touches `last_seen_at` on every request (wrapped so it never breaks the response). UA parsing failures degrade gracefully — the raw string is still stored.

**Endpoints** (all subclass `AdminConsoleAPIView`; scope = the caller's **own** sessions):

| Method + path | View | Purpose |
|---|---|---|
| `GET sessions/` | `AdminSessionListView` | List own live sessions (paginated), each with `is_current`. Rows whose `django_session` expired/vanished are filtered out and pruned. |
| `DELETE sessions/<int:pk>/` | `AdminSessionRevokeView` | Revoke one: `SessionStore(key).delete()` kills the browser + the record is removed. Not-own/unknown id → **404** (numeric id, no existence leak). Revoking your current session = self-logout. |
| `POST sessions/revoke-others/` | `AdminSessionRevokeOthersView` | Delete every own session except the current one ("log out everywhere else"); returns `revoked` count. |

Never trust a client-supplied `session_key`, IP, or UA — every field is captured server-side from `request`. Requires the `user-agents` dependency (`requirements.txt`).

## Known limitations

- **Mid-session deactivation** is not revoked instantly — a session stays valid until idle expiry. Hard revocation ("log this user out now") arrives with the user-management item.
- **2FA (TOTP)** is deferred — no library is installed yet.

## Backward compatibility

Purely additive: 7 endpoints under a new prefix (4 auth + 3 session-management), one model (`AdminSession`, migration `admin_console/0001_initial`), and the `user-agents` dependency. No existing endpoint, serializer, or auth path changed. The global DRF auth classes are untouched, so the JWT API keeps working with zero CSRF tokens. The only global change is session/CSRF cookie settings — sessions were previously used only by Django's `/admin/`, which is unaffected apart from a (desirable) shorter session lifetime.
