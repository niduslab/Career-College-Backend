# Postman Guide — Admin Console (Session Auth · Device Sessions · User Management)

Manual API testing for the platform admin back-office. Unlike the other guides in this folder
(which send a `Bearer` token), the admin console is **session-based**: you log in once, Postman holds
the `sessionid` + `csrftoken` cookies, and every later call reuses them — no `Authorization` header.
Writes additionally need a CSRF header (see *Session & CSRF in Postman* below).

Flow under test:

1. **Sign in** — establish an admin session via the shared platform login, then who-am-I and logout.
2. **My devices** — list the devices the admin is signed in from; revoke one / revoke all others.
3. **User management** — search accounts, view detail, suspend/reactivate, change role.
4. **Audit log** — confirm every account mutation is recorded.

> **Prerequisite:** an admin account. Create one with `python manage.py createsuperuser`
> (sets `is_staff`, `is_superuser`, `user_type='admin'`, `is_email_verified=True`), plus a couple of
> ordinary learner/instructor accounts to act on. Design reference:
> `docs/architecture/24-admin-console-auth.md`.

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `admin_email` / `admin_password` | `admin@example.com` / `…` | The superuser above |
| `csrf_token` | _(auto-captured)_ | Set by a test script on login (see below); sent as `X-CSRFToken` on writes |
| `refresh_token` | _(from login cookie)_ | The `refresh_token` cookie value; body of the full logout (1.3) |
| `target_user_id` | _(filled during tests)_ | A learner/instructor to suspend / role-change |
| `session_id` | _(filled during tests)_ | An `AdminSession` row id from the devices list |

---

## Session & CSRF in Postman

1. **Cookie jar on** (Postman default). After login, Postman automatically returns `sessionid` +
   `csrftoken` on every request to `localhost` — you do **not** add an `Authorization` header.
2. **Capture the CSRF token once.** On the login request (1.1), add this to the **Scripts →
   Post-response** tab so writes can echo the token back:
   ```javascript
   const csrf = pm.cookies.get('csrftoken');
   if (csrf) pm.environment.set('csrf_token', csrf);
   ```
   (Enable cookie access for `localhost` if Postman prompts.)
3. **On every unsafe method** (`POST`/`DELETE` after login) add header `X-CSRFToken: {{csrf_token}}`.
   Omitting it → `403` (that's CSRF protection working, not a bug).

---

## Access-Denied / Response Policy (applies throughout)

| Case | Response |
|---|---|
| Not logged in / no valid session on a base endpoint | **403** |
| Valid credentials but not an admin (login) | **403** "Only administrators can sign in here." |
| Bad/unknown credentials (login) | **400** generic "Invalid email or password." (no enumeration) |
| Missing `X-CSRFToken` on a write | **403** (CSRF) |
| Numeric-id resource not yours / not found (`sessions/<id>`, `users/<id>`) | **404** — ids never leak existence |
| Business-rule refusal (double-suspend, suspend an admin/self, role-change self, same role) | **422** |
| Too many mutations (> `ADMIN_ACTION_RATE_LIMIT`, default 30/min per admin) | **429** |
| `search` shorter than 2 chars, or non-boolean `is_staff` | **400** |

---

## Group 1: Sign in

### 1.1 Log in (the shared platform login)

The admin console has **no login endpoint of its own** — you sign in through the common platform
login, which additionally opens the admin session (and primes CSRF) when the user is an admin. Attach
the CSRF-capture script (above) to this request.

```
POST {{base_url}}/auth/login/
Content-Type: application/json

{
    "email": "{{admin_email}}",
    "password": "{{admin_password}}"
}
```

**Expect 200.** Response sets `sessionid` + `csrftoken` cookies (plus the usual JWT body/cookies).
A **non-admin** logging in here gets JWT only — no session cookies, so the admin-console endpoints
below reject them.

### 1.2 Who am I / is my session alive

```
GET {{base_url}}/admin-console/auth/session/
```
**Expect 200**, `data` includes the admin profile + `idle_timeout_seconds` (default 1800). Without a
live session → **403**.

### 1.3 Log out

Logout is the **shared** `POST /api/v1/auth/logout/` — the admin console has no logout of its own.
It kills the session **and** the JWT (blacklists the refresh token + clears the JWT cookies),
symmetric with the 1.1 login.

```
POST {{base_url}}/auth/logout/
Content-Type: application/json

{ "refresh": "{{refresh_token}}" }
```
**Expect 200.** A subsequent 1.2 → **403** (session gone). `refresh` is the `refresh_token` cookie
value from login (capture it with a script like the CSRF one, or paste it in).

> Note: `/auth/logout/` is a JWT endpoint (no `SessionAuthentication`), so it needs **no**
> `X-CSRFToken` header — CSRF only applies to the session-authenticated admin-console writes
> (Groups 2–3). Also, because an access token is stateless, a copy you kept keeps working until it
> expires (12 h) — logout can't revoke it; it only blacklists the refresh token.

### 1.4 Idle timeout (optional)

Set `ADMIN_SESSION_IDLE_TIMEOUT=60` in `.env`, restart, log in, wait > 60 s, then call 1.2 → **403**
(expired). Any request inside the window resets the clock (sliding expiry).

---

## Group 2: My devices (session tracking + remote logout)

Each admin login records the device (IP + parsed browser/OS). To see more than one row, log in from a
**second Postman environment** (or a separate browser) with a different `User-Agent`; Postman keeps
one cookie jar per domain, so a second signed-in client shows as a second device.

### 2.1 List my devices

```
GET {{base_url}}/admin-console/sessions/
```
**Expect 200**, paginated. Each row: `browser`, `os`, `device`, `ip_address`, `last_seen_at`,
`is_current` (`true` for the calling client). Save a non-current row's `id` → `session_id`. Expired
sessions are filtered out.

### 2.2 Revoke one device

```
DELETE {{base_url}}/admin-console/sessions/{{session_id}}/
X-CSRFToken: {{csrf_token}}
```
**Expect 200.** That device's 1.3 now → **403** (its session was deleted). Someone else's / unknown
id → **404**.

### 2.3 Log out everywhere else

```
POST {{base_url}}/admin-console/sessions/revoke-others/
X-CSRFToken: {{csrf_token}}
```
**Expect 200**, `data.revoked` = count killed. The current client stays alive.

---

## Group 3: User management

All endpoints need only an authenticated admin (session or JWT). Session-authed writes
(suspend/reactivate/role) still need the `X-CSRFToken` header; a JWT-authed admin doesn't (no session,
no CSRF).

### 3.1 List / search / filter / sort

```
GET {{base_url}}/admin-console/users/?search=learner@&user_type=learner&sort=-registration_date
```
**Expect 200**, paginated. Each row carries `user_type` + state flags (`is_active`,
`is_restricted_by_admin`, `is_deleted`, `is_staff`, …). Other params: `is_active` /
`is_restricted_by_admin` / `is_verified` / `is_email_verified` (`true`/`false`), `include_deleted=true`.
Bad `user_type` or `sort` → **400**. Save a learner's `id` → `target_user_id`.

### 3.2 User detail

```
GET {{base_url}}/admin-console/users/{{target_user_id}}/
```
**Expect 200** (includes soft-deleted accounts + `deleted_at`/`deletion_reason`). Unknown id → **404**.

### 3.3 Suspend

```
POST {{base_url}}/admin-console/users/{{target_user_id}}/suspend/
X-CSRFToken: {{csrf_token}}
Content-Type: application/json

{ "reason": "policy violation" }
```
**Expect 200** — sets `is_restricted_by_admin=True` + `is_active=False`. Verify the block: that user's
`POST {{base_url}}/auth/login/` now → **400** ("deactivated or restricted"). Suspending yourself,
another admin, or an already-suspended account → **422**.

### 3.4 Reactivate

```
POST {{base_url}}/admin-console/users/{{target_user_id}}/reactivate/
X-CSRFToken: {{csrf_token}}
```
**Expect 200**; that user can log in again. A not-suspended account → **422**.

### 3.5 Change role

```
POST {{base_url}}/admin-console/users/{{target_user_id}}/role/
X-CSRFToken: {{csrf_token}}
Content-Type: application/json

{ "user_type": "instructor" }
```
**Expect 200** — `user_type` switches and the matching profile is provisioned. Body may also carry
`"is_staff": true|false` to grant/revoke admin. Same role, or changing your **own** role → **422**;
invalid `user_type` → **400**.

---

## Group 4: Audit log

Every suspend / reactivate / role-change from Group 3 wrote an append-only row.

```
GET {{base_url}}/admin-console/audit/?action=suspend&target_user_id={{target_user_id}}
```
**Expect 200**, paginated. Rows carry `action`, `actor`, `target_user`, `reason`, `metadata`
(before/after snapshot), `created_at`. Filters: `action`, `target_user_id`, `actor_id`.
