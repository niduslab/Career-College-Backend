# 24 — Admin Console (Platform Back-Office)

**App:** `admin_console/` · **Prefix:** `/api/v1/admin-console/` · **Status:** implemented (Sprint 8 — session login, device/session tracking, user management)

## What this is

The **admin console** is the platform's internal back-office — the place a **platform administrator** (staff) signs in to run the platform itself, as opposed to the learner/instructor/institution app that everyone else uses. It's a JSON API; the admin front-end (a browser SPA) renders the screens and calls these endpoints.

Everything here is gated to admins only (`is_staff` or `user_type == 'admin'`). What an admin can do today:

1. **Sign in securely** to the back-office — a session-based login built for a browser tool, separate from the token login the public app uses.
2. **See and control their own devices** — which browsers/computers their admin account is signed in from, and "log this one out" / "log out everywhere else".
3. **Manage user accounts** — search everyone on the platform, suspend or reactivate accounts, change a user's role — with every such action written to an audit log.

More back-office features (content moderation, financial administration, platform configuration) are planned on top of the same foundation — see `docs/future_implementations/ADMIN_CONSOLE.md`. They all reuse one shared base view, `AdminConsoleAPIView`, so the auth/permission rules below apply to them automatically.

> This doc covers the **admin console app specifically**. For the cross-cutting map of *everything* a platform admin can do across all apps — course review, identity/institution verification review, category management, platform analytics — see [25 — Platform Admin Capabilities](25-admin-capabilities.md).

---

## Part 1 — Signing in

### Why a session login instead of the platform's JWT

The rest of the platform authenticates with a 12-hour JWT (JSON Web Token) — great for a mobile/SPA client that just wants a long-lived token in a header. A back-office is different: it's used from a browser and wants three things a long-lived token doesn't give cleanly —

- **Short idle timeout** — an unattended admin screen should lock itself, not stay open for 12 hours.
- **CSRF-protected writes** — because a browser sends its session cookie automatically, sensitive actions need a matching CSRF token so a malicious page can't trigger them.
- **"Log out everywhere"** — the ability to kill a session server-side on demand.

Django's built-in **sessions** give all three for free (the `django_session` table, already part of the schema — no new model needed just for auth). So the admin console runs on sessions; the public API keeps using JWT untouched.

### The one rule that keeps this from breaking the rest of the API

Session auth is turned on **per-view, only inside the admin console — never globally.** Django REST Framework enforces CSRF *only when a session authenticates the request*; if session auth were added to the global default, every existing JWT `POST`/`PATCH` on the whole platform would suddenly demand a CSRF token — an instant, silent breakage. So it lives on exactly one base class that every admin-console endpoint inherits:

```python
# admin_console/all_views/base.py
class AdminConsoleAPIView(APIView):
    authentication_classes = [SessionAuthentication, CookieJWTAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated, IsEmailVerified, IsPlatformAdmin]
```

Session-first, with a JWT fallback for automated tooling (a script with a `Bearer` token has no session, so DRF simply falls through to JWT — no CSRF needed for that path). **Every new admin-console endpoint subclasses this — never re-enable session auth globally.**

### How an admin gets a session

**The admin console has no login endpoint of its own.** Signing in goes through the one platform login, `POST /api/v1/auth/login/` — the single login for *everyone*. When the person logging in is an admin (`is_staff or user_type == 'admin'`), that view *additionally* opens a Django session and primes the CSRF cookie, on top of returning the usual JWT. So one login gives an admin both a JWT **and** a `sessionid` + `csrftoken` — no separate admin login step — while non-admins are completely unaffected (JWT only, no session). Because `UserLoginView` is `AllowAny` with no `SessionAuthentication`, opening the session there doesn't force CSRF on the login POST itself. The shared login is throttled the same way for everyone (`LoginThrottle`), which covers admins too.

Both **login and logout are the shared `/api/v1/auth/` endpoints** — the admin console has neither of its own. It exposes only one auth endpoint, the who-am-I check:

| Method + path | Purpose |
|---|---|
| `GET auth/session/` | "Who am I / am I still logged in?" — returns the admin's profile + the idle-timeout length, for the SPA to check on load. |

Logout is **symmetric with login**: since the shared login opens a session for admins, the shared logout `POST /api/v1/auth/logout/` also flushes it (`django_logout` when a session exists) on top of blacklisting the refresh token + clearing JWT cookies — otherwise the `sessionid` would outlive logout and keep the admin authenticated. JWT-only clients have no session, so that step is a no-op for them. (Note: an access token is stateless, so a copy the client kept stays valid until it expires — logout can't revoke it, only the refresh token is blacklisted.)

Login error handling (400 for bad credentials, account-state checks for deleted/suspended/unverified) all lives in that shared login and is documented in `02-auth-and-accounts.md`. A non-admin who logs in simply gets no session, so the admin-console endpoints reject them with **403** at the permission layer.

### Staying signed in — idle timeout and re-auth

- **Sliding idle timeout.** The session expires a set time after the admin's *last* request (default 30 min; env-tunable via `ADMIN_SESSION_IDLE_TIMEOUT`) and closes on browser exit. Each request resets the clock.
- **Step-up for sensitive actions (available, not yet used).** `IsRecentlyAuthenticatedAdmin` can require the admin to have logged in *recently* with a session (default within 15 min, `ADMIN_REAUTH_MAX_AGE`) before an action goes through. The mechanism ships, but no endpoint currently applies it — see the note in *Part 3*.

Cookies are hardened in settings (`HttpOnly` + `Secure` session cookie, `SameSite=Lax`, CSRF cookie readable by JS so the SPA can echo it back), and the login endpoint is throttled the same way the public login is.

---

## Part 2 — My devices (session tracking + remote logout)

An admin can see every device their account is signed in from and log any of them out remotely — the "you're signed in on 3 devices" screen. Django's session table alone can't power this: it records only a session key and expiry, not *what device* it is. So the console keeps a companion record, **`AdminSession`**, one row per signed-in browser, holding the human-readable detail:

| Field | What it holds |
|---|---|
| `user` | which admin |
| `session_key` | links to the underlying Django session |
| `ip_address` | the client IP (first proxy hop if forwarded) |
| `user_agent` + `browser` / `os` / `device` | the raw browser string, plus a parsed, friendly breakdown |
| `created_at` / `last_seen_at` | first seen / last active |

**How it's kept accurate, automatically:** a single login-signal handler records the device whenever an admin logs in (covering both login paths above, and even Django's own `/admin/` site). It ignores non-admins and token-only clients (which have no session). A logout handler removes the row, and each admin-console request quietly refreshes "last seen". None of this can break a login or a request — it's all best-effort, and device parsing failing just means the raw string is kept.

| Method + path | Purpose |
|---|---|
| `GET sessions/` | List *my own* active devices, flagging which one is the current browser. Dead/expired ones are hidden and cleaned up. |
| `DELETE sessions/<id>/` | Log out one device (deletes its session — that browser is signed out immediately). Someone else's id → 404, so ids can't be probed. |
| `POST sessions/revoke-others/` | "Log out everywhere else" — ends every session except the current one. |

An admin only ever sees or touches their **own** sessions, and every field is captured server-side — a client can't spoof an IP, device, or session key.

---

## Part 3 — User management + audit log

Where an admin administers the platform's accounts: find people, suspend troublemakers, reactivate them, and change roles. Every change that alters an account is recorded in an audit log.

All endpoints use the base admin gate (any authenticated admin — session or JWT); session-authed writes additionally need the CSRF header.

| Method + path | What it does |
|---|---|
| `GET users/` | Search + filter the whole user base (by email/name, role, verification/active state, including deleted), sorted and paginated. |
| `GET users/<id>/` | Full detail on one account, including soft-deleted ones (so admins can inspect closed accounts). |
| `POST users/<id>/suspend/` | Suspend an account (optional reason). |
| `POST users/<id>/reactivate/` | Lift a suspension. |
| `POST users/<id>/role/` | Change a user's role (`user_type`) and/or grant/revoke staff access. |
| `GET audit/` | Browse the admin-action audit log, filterable by target, actor, or action. |

> **Step-up re-auth is available but not currently applied here.** `IsRecentlyAuthenticatedAdmin` (`core/permissions.py`) can gate an endpoint on *how recently* the admin logged in with a session (within `ADMIN_REAUTH_MAX_AGE`, 15 min) — useful for genuinely destructive actions. The user-management mutations use the plain admin gate for now; wire in `IsRecentlyAuthenticatedAdmin` on any endpoint that later needs the stronger guard.

**Hardening.** The three mutations are **rate-limited per admin** (`ADMIN_ACTION_RATE_LIMIT`, default `30/min`) and take a `select_for_update` lock on the target so concurrent requests can't produce duplicate/interleaved audit rows. `is_staff` is strictly parsed (a stringy `"false"` is rejected, never silently truthy). Search requires ≥ 2 characters and is backed by **`pg_trgm` GIN indexes** on `User.email`/`full_name` (`authentication/0004`, built `CONCURRENTLY`) — otherwise `icontains` would force a full table scan. `pg_trgm` is already used by `courses` (trigram indexes on `NidusCourse`), so this adds no new extension dependency; the migration's `TrigramExtension()` is a no-op where it already exists.

### Suspending an account — why it flips two switches

A suspension has to actually lock the person out — both stopping new logins *and* invalidating the session/token they might already hold. Because of how the two auth systems behave, one flag isn't enough:

- `is_restricted_by_admin = True` — every login path refuses this account, and (unlike a self-service deactivation) the user can't undo it themselves. But an already-issued JWT wouldn't notice this flag.
- `is_active = False` — this *is* re-checked on every JWT request, so it also kills tokens the user already has (on their next call).

So **suspend sets both**; reactivate clears both. Guard rails: an admin can't suspend themselves, can't suspend another admin, and can't double-suspend — all rejected with a clear error.

### Changing a role — and the profile that has to follow

Each account type (learner, instructor, partner institution) has a matching profile record, and that profile is normally created once, at sign-up. Changing someone's role later would leave them without the right profile. So the role-change path **provisions the new profile** using the same shared helper the sign-up flow uses (`ensure_profile_for_type` — now the single source of truth for "make the profile for this account type"). The old profile is left in place rather than deleted, because deleting it would cascade and destroy real content (their courses, enrollments). An admin can't change their *own* role (no accidental self-lockout).

### The audit log

Every suspend / reactivate / role-change writes one **append-only** `AdminActionLog` row — who did it, to whom, what action, an optional reason, and a before/after snapshot — in the *same database transaction* as the change, so the log can never disagree with reality. The mutation takes a `select_for_update` lock on the target row, so concurrent requests can't produce duplicate or interleaved audit rows. The actor/target FKs are `SET_NULL`, so the row also **snapshots both emails into `metadata`** (`actor_email` / `target_email`) — attribution survives even if either account is later deleted. It's read-only even in Django's own admin site. This is the seed of the platform-wide audit trail the roadmap calls for; today it covers user-management actions.

---

## What's intentionally not built yet

- **Instant token kill on suspend.** Suspend now blacklists every outstanding **refresh** token (in the suspend transaction) and flips `is_active`, which JWT honors on the user's *next* request. What's still not possible is revoking a *stateless access token* mid-life — a copy the user already holds keeps working until it expires (12 h). That's a property of JWT, not a missing feature.
- **2FA (TOTP)** — deferred; no library wired in.
- **Support tickets / disputes** — not built (see `docs/future_implementations/ADMIN_CONSOLE.md` §1). The **"your account was suspended/reactivated" email** is now sent (`ACCOUNT_SUSPENDED`/`ACCOUNT_REACTIVATED`, unmutable critical notices).

## Impact on the rest of the platform

This app is purely additive — a new URL prefix (10 endpoints: 1 who-am-I + 3 device-session + 5 user-management + 1 audit), its own two models (`AdminSession`, `AdminActionLog`), and the `user-agents` dependency. It deliberately has **no login or logout endpoint** — admins sign in and out through the shared platform `/api/v1/auth/` endpoints, which were taught to open/flush a session for them. The only change to existing code is that shared login gaining the admin-session hook, plus an internal, behavior-neutral refactor: profile creation on sign-up now goes through the shared `ensure_profile_for_type` helper. The global authentication config is untouched, so the JWT API keeps working with no CSRF tokens; the only platform-wide change is the session/CSRF cookie settings, which previously affected only Django's `/admin/` site (now with a shorter, safer session lifetime).

---

## Where the code lives

| Concern | Files |
|---|---|
| Shared base view (auth + permission rules) | `admin_console/all_views/base.py` |
| Who-am-I (`auth/session/`) | `admin_console/all_views/auth_views.py` |
| Login + logout (open/flush the admin session) | shared `UserLoginView` / `LogoutView` in `authentication/all_views/auth_views.py` |
| Device tracking | `admin_console/all_models/session_models.py`, `admin_console/signals.py`, `admin_console/all_views/session_views.py` |
| User management + audit | `admin_console/services/user_admin_service.py`, `admin_console/all_views/user_views.py`, `admin_console/all_models/user_admin_models.py` |
| Permissions | `core/permissions.py` (`IsPlatformAdmin`, `IsRecentlyAuthenticatedAdmin`) |
| Shared profile provisioning | `authentication/services/profile_service.py` |
| Settings | session/CSRF hardening + `ADMIN_*` knobs in `career_college_backend/settings.py` |

Manual walkthrough (Postman): `docs/api-testing/postman-admin-console.md`.
