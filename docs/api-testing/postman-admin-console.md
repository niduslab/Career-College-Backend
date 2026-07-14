# Postman / curl — Admin Console Session Auth

Manual walkthrough for the session-based admin login. Base URL: `http://localhost:8000/api/v1/admin-console`.

**Cookie jar required.** Session auth relies on the `sessionid` and `csrftoken` cookies. In Postman, keep the cookie jar on (default). In curl, use `-c cookies.txt -b cookies.txt` on every call.

Create an admin first: `python manage.py createsuperuser` (this sets `is_staff`, `is_superuser`, `user_type='admin'`, `is_email_verified=True`).

## 0. Preferred: log in via the shared login (one call)

The common login `POST /api/v1/auth/login/` is the single login for every role. For an admin it returns JWT **and** opens the session — one call sets both `sessionid` and `csrftoken`, so you can skip the dedicated `auth/csrf/` + `auth/login/` steps (§1–§2) entirely.

```bash
curl -c cookies.txt -b cookies.txt \
  -X POST http://localhost:8000/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@example.com", "password": "your-password"}'
```
→ `200 {"success": true, "message": "Login successful.", "data": {...}}` and `Set-Cookie: sessionid=...; csrftoken=...` (plus the JWT `access_token`/`refresh_token` cookies + body).

Then jump straight to §3 (who-am-I) / §4 (CSRF-protected logout) using the same cookie jar. A **non-admin** login here returns JWT only — no `sessionid`/`csrftoken`.

The dedicated admin-console login below (§1–§2) still works; it's the session-only path (no JWT) if you want that.

## 1. Prime the CSRF cookie

```bash
curl -c cookies.txt -b cookies.txt http://localhost:8000/api/v1/admin-console/auth/csrf/
```
→ `200 {"success": true, "message": "CSRF cookie set."}` and `Set-Cookie: csrftoken=...`.

## 2. Log in

```bash
curl -c cookies.txt -b cookies.txt \
  -X POST http://localhost:8000/api/v1/admin-console/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@example.com", "password": "your-password"}'
```
→ `200 {"success": true, "message": "Login successful.", "data": {"user_id", "email", "full_name", "user_type", "is_staff"}}` and `Set-Cookie: sessionid=...`.

Login itself needs **no** CSRF token (no session yet — credentials + throttle protect it).

**Failure cases:**
- Valid credentials but not an admin (e.g. a learner) → `403` "Only administrators can sign in here."
- Wrong/unknown password → `400` "Invalid email or password." (generic — no enumeration).
- Restricted/inactive/unverified account → `400` with the matching message.

## 3. Check the session (who-am-I)

```bash
curl -b cookies.txt http://localhost:8000/api/v1/admin-console/auth/session/
```
→ `200 {"success": true, "data": {"user_id", ..., "idle_timeout_seconds": 1800}}`.

Without a valid session cookie → `403`.

## 4. Log out (CSRF-protected)

Logout is a session-authenticated `POST`, so it needs the CSRF header. Read the `csrftoken` cookie value and send it as `X-CSRFToken`:

```bash
CSRF=$(grep csrftoken cookies.txt | awk '{print $7}')
curl -b cookies.txt -X POST \
  -H "X-CSRFToken: $CSRF" \
  http://localhost:8000/api/v1/admin-console/auth/logout/
```
→ `200 {"success": true, "message": "Logged out."}`. A subsequent `GET auth/session/` → `403` (session flushed).

Omitting `X-CSRFToken` → `403` CSRF failure (this is the CSRF protection working).

## 5. Idle timeout

Set `ADMIN_SESSION_IDLE_TIMEOUT=60` in `.env`, restart, log in, wait > 60 s, then hit `auth/session/` → `403` (session expired). Each request within the window resets the clock (sliding expiry).

## 6. JWT fallback (automated tooling)

The base view also accepts a `Bearer` token, so scripts don't need a session:

```bash
curl http://localhost:8000/api/v1/admin-console/auth/session/ \
  -H "Authorization: Bearer <admin-access-token>"
```
→ `200`. A non-admin token → `403`. (JWT clients carry no session, so no CSRF token is required.)

## 7. Device / session tracking + remote logout

Every admin login records an `AdminSession` (IP + parsed browser/OS/device), keyed to the `sessionid`. These endpoints let an admin see and revoke their own devices. Use a **second cookie jar** (`cookies2.txt`) with a distinct `-A` user-agent to simulate a second device:

```bash
# Second device logs in (own jar, different UA)
curl -c cookies2.txt -b cookies2.txt \
  -A "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0" \
  -X POST http://localhost:8000/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email": "admin@example.com", "password": "your-password"}'
```

**List your sessions** (from device 1):
```bash
curl -b cookies.txt http://localhost:8000/api/v1/admin-console/sessions/
```
→ `200` paginated envelope; two rows, each with `browser`, `os`, `device`, `ip_address`, `last_seen_at`, and `is_current` (`true` for the calling jar). Expired sessions are filtered out.

**Revoke one device** (CSRF-protected `DELETE`; take an `id` where `is_current=false`):
```bash
CSRF=$(grep csrftoken cookies.txt | awk '{print $7}')
curl -b cookies.txt -X DELETE \
  -H "X-CSRFToken: $CSRF" \
  http://localhost:8000/api/v1/admin-console/sessions/<id>/
```
→ `200`. That device's `auth/session/` now returns `403` (its session was deleted). A non-own / unknown id → `404` (never `403` — numeric ids don't leak existence).

**Log out everywhere else** (kills all sessions except the current jar):
```bash
CSRF=$(grep csrftoken cookies.txt | awk '{print $7}')
curl -b cookies.txt -X POST \
  -H "X-CSRFToken: $CSRF" \
  http://localhost:8000/api/v1/admin-console/sessions/revoke-others/
```
→ `200 {"success": true, ..., "data": {"revoked": N}}`. The current jar stays alive.
