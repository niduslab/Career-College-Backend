# Postman Guide — Platform Admin (full capability surface)

Manual API testing for **everything a platform admin can do** — mapped 1:1 against
`docs/architecture/25-admin-capabilities.md`. Admin power isn't one app; it spans the session-based
**admin console** plus several JWT-authed surfaces (course review, verification review, category
management, platform analytics, webinar archive). This guide covers all of them.

**Two auth shapes — read this first, it explains the whole guide:**

| Surface | How Postman authenticates | CSRF header on writes? |
|---|---|---|
| **Admin console** (`/admin-console/...`, Groups 1–4) | the `sessionid` cookie from login (session auth) | **yes** — `X-CSRFToken` |
| **Everything else** (Groups 5–9) | the `access_token` cookie from the *same* login (JWT via `CookieJWTAuthentication`) | **no** — JWT auth never triggers CSRF |

The key convenience: **one login gives you both.** The shared platform login (1.1) sets `sessionid` +
`csrftoken` **and** `access_token` cookies. Postman's cookie jar sends whichever the endpoint needs — the
console reads the session, the JWT surfaces read the access-token cookie — so after step 1.1 you don't add
an `Authorization` header anywhere. (If you prefer, you can instead send `Authorization: Bearer {{jwt_access}}`
on Groups 5–9 — those endpoints accept the header too. The console does not: it wants the session.)

Flow under test:

1. **Sign in** — establish an admin session via the shared platform login, then who-am-I and logout.
2. **My devices** — list the devices the admin is signed in from; revoke one / revoke all others.
3. **User management** — search accounts, view detail, suspend/reactivate, change role.
4. **Audit log** — confirm every account mutation is recorded.
5. **Course review** — browse the pending-review queue, approve/reject a course, archive/restore any course.
6. **Verification review** — review instructor identity and partner-institution verifications.
7. **Course categories** — create / edit / soft-deactivate the public category tree.
8. **Platform analytics** — read the system-wide KPI + trend dashboard.
9. **Webinar archive** — archive any webinar (admin override of owner scope).

> **Prerequisite:** an admin account. Create one with `python manage.py createsuperuser`
> (sets `is_staff`, `is_superuser`, `user_type='admin'`, `is_email_verified=True`), plus a couple of
> ordinary learner/instructor accounts to act on. Groups 5–9 also need some real data to act on — a course
> sitting in `under_review`, a submitted verification, a published webinar — so seed those first (or run the
> relevant author-side Postman guide up to the submit step). Design reference:
> `docs/architecture/25-admin-capabilities.md` (full map) and `24-admin-console-auth.md` (console internals).

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
| `jwt_access` | _(from login cookie/body)_ | Optional — only if you send `Authorization: Bearer` on Groups 5–9 instead of the cookie |
| `course_id` | _(filled during tests)_ | A course in `under_review` (Group 5) |
| `verification_id` | _(filled during tests)_ | An `IdentityVerification` row id (Group 6) |
| `inst_verification_id` | _(filled during tests)_ | An `InstitutionVerification` row id (Group 6) |
| `category_id` | _(filled during tests)_ | A `CourseCategory` row id (Group 7) |
| `webinar_id` | _(filled during tests)_ | A published `Webinar` row id (Group 9) |

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

The CSRF and session rows above are **console-only** (Groups 1–4). For the JWT surfaces (Groups 5–9): no
CSRF ever; a non-admin (or logged-out) caller → **403**; and the project-wide 403-vs-404 rule applies —
**numeric-id** resources (`courses/<pk>/`, `verification/admin/<pk>/`, `categories/<pk>/`, `webinars/<pk>/`)
return **404** on no-access so ids can't be probed, while a business-rule refusal (illegal state transition)
returns **422**.

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

Suspend does two more things you can verify:
- **Kills existing tokens.** `is_active=False` makes SimpleJWT reject the user's current access token on its
  next call, and every outstanding **refresh** token is blacklisted in the same transaction — so
  `POST {{base_url}}/auth/token/refresh/` with their old refresh token now → **401**, they can't mint a fresh
  access token. (A copy of a still-valid access token keeps working until it expires — nothing can revoke a
  stateless token mid-life.)
- **Emails the user.** An `ACCOUNT_SUSPENDED` notification (in-app + email) fires on commit. It is a critical
  account notice, so it is sent even though the account is now inactive and regardless of email preferences
  (unmutable). Watch the Celery/console email backend to confirm.

### 3.4 Reactivate

```
POST {{base_url}}/admin-console/users/{{target_user_id}}/reactivate/
X-CSRFToken: {{csrf_token}}
```
**Expect 200**; that user can log in again (clears both `is_restricted_by_admin` and `is_active`) and gets an
`ACCOUNT_REACTIVATED` notification (in-app + email, also unmutable). The guard keys on `is_restricted_by_admin`,
so an account deactivated for a **non-suspension** reason is **not** silently re-activated → **422** ("not
suspended"). A never-suspended account → **422**.

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

---

# JWT admin surfaces (Groups 5–9)

Everything below is **JWT-authed**, not session-authed. After the 1.1 login Postman's cookie jar already
holds the `access_token` cookie, so these calls just work — **no `X-CSRFToken` header** and no
`Authorization` header needed. (Bearer works too if you'd rather: `Authorization: Bearer {{jwt_access}}`.)
These endpoints share the exact same permission (`IsPlatformAdmin`) as the console but skip the session/CSRF
machinery, because CSRF only matters for cookie-session auth.

---

## Group 5: Course review

The admin is the final gate before a course goes public. **Setup:** have an instructor author a course and
submit it (`POST /courses/<pk>/submit/`) so it sits in `status=under_review`.

### 5.1 Pending-review queue

```
GET {{base_url}}/courses/admin/pending-review/
```
**Expect 200**, paginated, oldest-submitted-first — every course in `under_review`. Optional
`?delivery_mode=self_paced|scheduled` filter (unknown value → **400**). Save a course `id` → `course_id`.

### 5.2 Approve or reject

```
POST {{base_url}}/courses/{{course_id}}/review/
Content-Type: application/json

{ "action": "approve" }
```
**Expect 200** — course → `published` (and any draft schedules auto-activate); the author gets a
`COURSE_APPROVED` notification. To reject instead, send `{ "action": "reject", "rejection_reason": "…" }`
→ course goes to `rejected` (author reworks) + `COURSE_REJECTED`. `action` other than `approve`/`reject`
→ **400**. Reviewing a course **not** in `under_review` → **422** (illegal transition). Unknown `pk` → **404**.

### 5.3 Archive / restore any course (admin override)

These aren't admin-only endpoints — an owner can archive/restore their own course — but an admin may act on
**any** course (inline elevation).

```
POST {{base_url}}/courses/{{course_id}}/archive/
POST {{base_url}}/courses/{{course_id}}/restore/
```
**Expect 200.** Archive takes a `published` course → `archived`; restore takes `archived` → `draft`. As a
non-owner admin you can still target any course; a non-admin non-owner → **404**.

---

## Group 6: Verification review

Admins review two independent state machines: instructor **identity** verification and partner-**institution**
verification. Same shape for both — list, detail, review. **Setup:** have an instructor submit an identity
verification and/or an institution submit its credential verification (see the partner-institution guide).

### 6.1 Identity verification — list & detail

```
GET {{base_url}}/verification/admin/list/?status=submitted
GET {{base_url}}/verification/admin/{{verification_id}}/
```
**Expect 200.** The list is paginated and filterable by `?status=` (`submitted`, `under_review`, …). Save a
row `id` → `verification_id`.

### 6.2 Identity verification — review

```
POST {{base_url}}/verification/admin/{{verification_id}}/review/
Content-Type: application/json

{ "action": "approve", "admin_notes": "looks good" }
```
`action` ∈ `pick_up` (→ `under_review`), `approve`, `reject`, `request_action`, `expire`.
**Expect 200.** `approve` auto-sets `InstructorProfile.is_verified=True` and notifies the user
(`VERIFICATION_APPROVED`). `reject` **requires** `rejection_reason` (else **400**); `request_action`
**requires** `action_required_reason` (else **400**). An illegal transition for the row's current status
→ **422**. Unknown `pk` → **404**.

### 6.3 Institution verification — list, detail, review

```
GET  {{base_url}}/verification/admin/institution/list/?status=submitted
GET  {{base_url}}/verification/admin/institution/{{inst_verification_id}}/
POST {{base_url}}/verification/admin/institution/{{inst_verification_id}}/review/
Content-Type: application/json

{ "action": "approve" }
```
Same as 6.2 with one difference: institutions have **no `expire`** action — sending `expire` → **422**
("not valid for institutions"). `approve` sets `PartnerInstitutionProfile.is_verified=True` + `is_active=True`
and notifies the institution (`INST_VERIFICATION_APPROVED`); `reject` needs `rejection_reason`.

---

## Group 7: Course categories

Public to read, admin to write — the same URL serves both.

### 7.1 Public list (no auth needed)

```
GET {{base_url}}/courses/categories/
```
**Expect 200**, paginated nested tree (top-level categories each with active `children`). This one is
`AllowAny` — works logged out.

### 7.2 Create

```
POST {{base_url}}/courses/categories/
Content-Type: application/json

{ "name": "Data Science" }
```
**Expect 201.** `slug` auto-fills from `name` if omitted. Optional `"parent": <id>` nests it under a
top-level category (the tree is **2 levels only** — a parent that itself has a parent → **400**). Duplicate
name/slug → **422**. Save `id` → `category_id`. A **non-admin** POST → **403**.

### 7.3 Detail / update / deactivate

```
GET    {{base_url}}/courses/categories/{{category_id}}/
PATCH  {{base_url}}/courses/categories/{{category_id}}/     { "name": "Data Science & ML" }
DELETE {{base_url}}/courses/categories/{{category_id}}/
```
All three are **admin-only** (GET included, unlike 7.1). **Expect 200.** DELETE is a **soft**
deactivate (`is_active=False`) — the row stays, just drops out of the public tree. Unknown `pk` → **404**.

---

## Group 8: Platform analytics

Read-only, system-wide dashboard (whole platform, no institution filter). All GET, all `IsPlatformAdmin`.

```
GET {{base_url}}/analytics/admin/summary/
GET {{base_url}}/analytics/admin/users/trend/?granularity=monthly&periods=6
GET {{base_url}}/analytics/admin/enrollments/trend/
GET {{base_url}}/analytics/admin/certificates/trend/
GET {{base_url}}/analytics/admin/revenue/trend/
GET {{base_url}}/analytics/admin/top-courses/?sort=enrollments&limit=10
GET {{base_url}}/analytics/admin/funnel/
```
**Expect 200** on each. `summary` returns KPI cards (users / courses / enrollments / certificates / webinars
/ **revenue**). Unlike the partner dashboard, admin **revenue is real** (`revenue.enabled: true`, summed from
paid orders). Trend `periods` clamps to `[1, 24]`; `top-courses` `limit` to `[1, 50]`; `funnel` returns the
distinct-learner signup → enrolled → completed → certified counts. A non-admin on any of these → **403**.

---

## Group 9: Webinar archive (admin override)

Not an admin-only endpoint — an owner/host archives their own webinar — but an admin may archive **any**
webinar (inline elevation, same pattern as 5.3). **Setup:** a published webinar owned by someone else.

```
POST {{base_url}}/webinars/{{webinar_id}}/archive/
```
**Expect 200** — `published` → `archived`. As admin you can target any webinar; a non-admin non-owner/host
→ **404**. This is the only admin reach into the webinars app (publish/rework stay owner/host-scoped).
