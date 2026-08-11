# 25 — Platform Admin Capabilities (Cross-Cutting Overview)

**Audience:** platform administrators (`is_staff` **or** `user_type == 'admin'`) · **Status:** implemented (spread across several apps)

## What this is

Admin power on this platform is **not** one app — it's a capability that cuts across several. This doc is the single map of *everything a platform admin can do today*, who guards each surface, and where the deep design rationale lives. It's the "what can an admin touch, and where's the code" reference; each area has its own detailed doc linked at the point it's described.

There are two distinct authentication shapes an admin operates under, and knowing which is which explains the whole layout:

| Surface | Auth | CSRF | Where |
|---|---|---|---|
| **Admin console** (`/api/v1/admin-console/`) | Django **session** (session-primary, JWT fallback) | required on writes | `admin_console/` — see [24](24-admin-console-auth.md) |
| **Everything else an admin does** (course review, verification review, category management, platform analytics) | plain **JWT** (`Bearer` / cookie) | none | scattered across `courses/`, `id_verification/`, `analytics/` |

Both are reached with the **same login** — the shared `POST /api/v1/auth/login/`. When the person is an admin it returns the usual JWT **and** additionally opens a session + primes CSRF (see [24](24-admin-console-auth.md) §"How an admin gets a session"). So the JWT the admin already holds authenticates the non-console admin endpoints below, while the session cookie authenticates the console. One sign-in, both surfaces.

---

## The permission classes (all in `core/permissions.py`)

Every admin surface gates on one of these — **never** an ad-hoc `is_staff` check in a view:

| Class | Passes when | Used by |
|---|---|---|
| `IsPlatformAdmin` | `is_staff` **or** `user_type == 'admin'` | course review, verification review, category CRUD, admin analytics |
| `IsRecentlyAuthenticatedAdmin` | `IsPlatformAdmin` **and** the session's `admin_login_at` is within `ADMIN_REAUTH_MAX_AGE` (default 15 min) | **available, not currently wired to any endpoint** — reserved for step-up re-auth on genuinely destructive actions |
| `IsAdminOrReadOnly` | write requires `is_staff`; read open | **defined but unused** by any admin endpoint mapped here |
| `AdminConsoleAPIView` (base view, `admin_console/all_views/base.py`) | `IsAuthenticated + IsEmailVerified + IsPlatformAdmin`, with `SessionAuthentication` first | every admin-console endpoint |

`IsPlatformAdmin` is the workhorse. It accepts either a session-authed admin (browser SPA) or a JWT-authed admin (tooling), so the same class works for both auth shapes.

### Two ways an endpoint becomes admin-elevated

Most admin capability is a **permission-class gate** (`IsPlatformAdmin`, or the `AdminConsoleAPIView` base) — the endpoint is admin-only, full stop. But three endpoints use a second mechanism: an **inline `is_staff`/`user_type=='admin'` branch inside an otherwise owner-scoped view**, which *widens* the view for an admin rather than restricting it. These stay open to the resource owner and additionally let an admin act on *any* resource:

- `CourseArchiveView` (`status_views.py:489`) / `CourseRestoreView` (`:528`) — owner can archive/restore their own course; an admin can archive/restore **any** course.
- `WebinarArchiveView` (`webinars/all_views/status_views.py:125`) — owner/host can archive their own webinar; an admin can archive **any** webinar.

When auditing "what can an admin touch", both mechanisms count — grepping only for `IsPlatformAdmin` misses these three.

---

## Capability map — everything an admin can do

### 1. Account administration & the audit log — `admin_console/` (session)

The back-office proper. Search/inspect every account, suspend/reactivate, change roles, manage own devices, read the audit trail. **This is the only surface that uses session auth + CSRF.** Full design, endpoint list, and the "suspend flips two switches" reasoning are in **[24 — Admin Console](24-admin-console-auth.md)**; manual walkthrough in [postman-admin-console.md](../api-testing/postman-admin-console.md).

One-line summary of the endpoints (all subclass `AdminConsoleAPIView`, own-sessions-only where applicable, numeric id → 404):

| Method + path | Purpose |
|---|---|
| `GET auth/session/` | Who-am-I / is my session alive |
| `GET/DELETE sessions/[<id>/]`, `POST sessions/revoke-others/` | List my devices; remote-logout one / all others |
| `GET users/[<id>/]` | Search/inspect accounts (incl. soft-deleted) |
| `POST users/<id>/suspend/` | Suspend — sets `is_restricted_by_admin=True` + `is_active=False`, blacklists all refresh tokens, emails the user (`ACCOUNT_SUSPENDED`) |
| `POST users/<id>/reactivate/` | Lift an admin suspension (`ACCOUNT_REACTIVATED`) |
| `POST users/<id>/role/` | Change `user_type` (provisions the new profile) and/or `is_staff` |
| `GET audit/` | Append-only `AdminActionLog` (actor/target/action/reason/before-after; emails snapshotted) |

> **Note on suspend (kept in sync with code):** suspend now **blacklists every outstanding refresh token** inside the suspend transaction and **dispatches an `ACCOUNT_SUSPENDED` notification** on commit; reactivate dispatches `ACCOUNT_REACTIVATED`. Both events are deliberately absent from `EVENT_TO_CATEGORY` (critical account notices — always emailed, unmutable), and `ACCOUNT_SUSPENDED` is on `send_notification_email_task`'s inactive-recipient allowlist so the email isn't dropped when the account goes inactive. The only remaining gap is that a *stateless access token* the user already holds keeps working until it expires (12 h) — nothing can revoke it mid-life; `is_active=False` blocks it on the *next* request.

Code: `admin_console/services/user_admin_service.py`, `admin_console/all_views/{user,session,auth}_views.py`, `admin_console/all_models/{session,user_admin}_models.py`, `admin_console/signals.py`.

### 2. Course review (approve / reject / queue) — `courses/` (JWT)

An admin is the final gate before a course goes public. Courses submitted for review (`status=under_review`) land in the admin's queue; the admin approves (→ `published`) or rejects (→ `rejected`, back to the author to rework).

| Method + path | View (`courses/all_views/status_views.py`) | Purpose |
|---|---|---|
| `GET /api/v1/courses/admin/pending-review/` | `CourseAdminPendingReviewListView` | The discovery queue — all `under_review` courses, oldest-first, paginated; optional `?delivery_mode=self_paced\|scheduled` |
| `GET /api/v1/courses/<pk>/review/` | `CourseAdminReviewView` | Course detail for review: base course fields (`CourseAdminReviewDetailSerializer`) + attached schedules + `outline_stats` (section/content counts) — no ownership filter |
| `POST /api/v1/courses/<pk>/review/` | `CourseAdminReviewView` | Body `{action: "approve"\|"reject", rejection_reason?}`. Approve → `published`; reject → `rejected` |
| `GET /api/v1/courses/<pk>/review/curriculum/` | `CourseAdminCurriculumView` | Full curriculum tree — sections → lectures/quizzes/assignments/coding exercises, at the **same depth an instructor sees** (video + article content, quiz correct answers, assignment model answers/rubric, coding solution code + evaluation script). Lets an admin actually judge the content before approving, not just metadata. Read-only; loader is `curriculum_service.load_admin_review_curriculum()`, distinct from the learner-safe/catalog loaders in the same module. |

All three gated `IsPlatformAdmin`. The status change goes through `NidusCourse.transition_to()` (single entry point — never set `status` directly), which notifies the author on commit (`COURSE_APPROVED`/`COURSE_REJECTED`); approving also auto-activates the course's draft schedules. The two-stage flow for institution-owned courses (expert `/finish/` → institution forwards → admin `/review/`) is the author/institution side; the admin only ever sees `under_review`. Full state machine in **[11 — Course Lifecycle](11-course-lifecycle.md)**.

> **Archive/restore** (`POST /<pk>/archive/`, `/restore/`) are the inline-elevation kind (see above): gated only `IsEmailVerified`, with an admin branch that lets an admin archive/restore **any** course, while a non-admin is limited to their own. Not admin-exclusive, but an admin *can* act on any course — listed here so the capability isn't missed.

### 3. Identity & institution verification review — `id_verification/` (JWT)

Admins are the reviewers in both verification state machines: instructor **identity** verification and partner-**institution** verification. Each has list / detail / review endpoints, all `IsPlatformAdmin`.

| Method + path | View (`id_verification/all_views/admin_views.py`) | Purpose |
|---|---|---|
| `GET /api/v1/verification/admin/list/` | `AdminVerificationListView` | Instructor identity submissions (`?status=` filter) |
| `GET /api/v1/verification/admin/<pk>/` | `AdminVerificationDetailView` | One identity submission |
| `POST /api/v1/verification/admin/<pk>/review/` | `AdminVerificationReviewView` | Transition it: `pick_up`/`approve`/`reject`/`request_action`/`expire` |
| `GET /api/v1/verification/admin/institution/list/` | `AdminInstitutionVerificationListView` | Institution submissions |
| `GET /api/v1/verification/admin/institution/<pk>/` | `AdminInstitutionVerificationDetailView` | One institution submission |
| `POST /api/v1/verification/admin/institution/<pk>/review/` | `AdminInstitutionVerificationReviewView` | Transition it: `pick_up`/`approve`/`reject`/`request_action` (**no `expire`** for institutions) |

The `action → status` maps live at the top of `admin_views.py`. Approving an identity verification auto-sets `InstructorProfile.is_verified=True`; approving an institution sets `PartnerInstitutionProfile.is_verified=True` + `is_active=True`. Every decision notifies the subject on commit (`VERIFICATION_*` / `INST_VERIFICATION_*`). Design in **[07 — ID Verification](07-id-verification.md)** and **[18 — Partner Institutions](18-partner-institutions.md)**.

### 4. Course-category management — `courses/` (JWT)

The category taxonomy is public to read but admin-only to write. An admin owns the category tree.

| Method + path | View (`courses/all_views/category_views.py`) | Purpose |
|---|---|---|
| `GET /api/v1/courses/categories/` | `CourseCategoryListCreateView` (`AllowAny` on GET) | Public nested category tree |
| `POST /api/v1/courses/categories/` | `CourseCategoryListCreateView` (`IsPlatformAdmin` on POST) | Create a category |
| `GET/PATCH/DELETE /api/v1/courses/categories/<pk>/` | `CourseCategoryDetailView` (`IsPlatformAdmin`) | Inspect / edit / soft-deactivate (DELETE sets `is_active=False`) |

Note the mixed-permission pattern: `CourseCategoryListCreateView.get_permissions()` returns `AllowAny` for GET and the admin gate for POST — a public read surface with an admin write surface on the same URL.

### 5. Platform-wide analytics — `analytics/` (JWT)

The system-wide dashboard: KPIs and trends across the *whole platform* (no institution filter — that's the partner surface). Gated `[IsAuthenticated, IsEmailVerified, IsPlatformAdmin]` on plain `APIView`s (the SPA's JWT reaches them; no dependency on `AdminConsoleAPIView`).

| Method + path | View (`analytics/all_views/admin_analytics_views.py`) | Purpose |
|---|---|---|
| `GET /api/v1/analytics/admin/summary/` | `AdminAnalyticsSummaryView` | Platform KPIs: users / courses / enrollments / certificates / webinars / **revenue** |
| `GET /api/v1/analytics/admin/users/trend/` | `AdminUserTrendView` | New-signup time series |
| `GET /api/v1/analytics/admin/enrollments/trend/` | `AdminEnrollmentTrendView` | Enrollment time series |
| `GET /api/v1/analytics/admin/certificates/trend/` | `AdminCertificateTrendView` | Certificate-issuance time series |
| `GET /api/v1/analytics/admin/revenue/trend/` | `AdminRevenueTrendView` | Paid-order gross **sum** per bucket |
| `GET /api/v1/analytics/admin/top-courses/` | `AdminTopCoursesView` | Ranked platform courses (`?sort=`/`?limit=`) |
| `GET /api/v1/analytics/admin/funnel/` | `AdminFunnelView` | Distinct-learner funnel: signup → enrolled → completed → certified |

Unlike the partner dashboard, admin **revenue is real** (`revenue.enabled: True`, summed from `payments.Order` where `status='paid'`) — at platform scope there's no per-institution attribution problem. Design in **[20 — Analytics Dashboard](20-analytics-dashboard.md)**; walkthrough in [postman-analytics.md](../api-testing/postman-analytics.md).

### 6. Webinar archive (inline elevation) — `webinars/` (JWT)

| Method + path | View | Purpose |
|---|---|---|
| `POST /api/v1/webinars/<pk>/archive/` | `WebinarArchiveView` (`webinars/all_views/status_views.py:118`) | Owner/host archives their own webinar; an **admin** can archive **any** webinar (inline `is_staff`/`admin` branch at `:125`) |

This is the only admin reach into the webinars app — publish/rework stay owner/host-scoped. See **[19 — Webinars](19-webinars.md)**.

### 7. Django's built-in `/admin/` site

`/admin/` (Django's own admin) is still mounted. `AdminActionLog` and `AdminSession` are registered there **read-only**. It shares the same hardened session cookie settings as the console (see [24](24-admin-console-auth.md) §settings), so a login there also gets recorded as a device by the `user_logged_in` signal handler. This is a low-level operational fallback, not the product back-office.

---

## Cross-cutting rules that apply to every admin surface

- **403 vs 404.** The project-wide access-denied policy applies: slug-identified resources → 403, numeric-id resources → 404 (ids aren't public-enumerable). Admin endpoints that take a numeric `pk` return 404 on no-access, never a leak. See CLAUDE.md §"403 vs. 404 Access-Denied Policy".
- **State changes go through `transition_to()`.** Course review and both verification reviews mutate status **only** via the model's `transition_to()` — never a direct `status =` assignment. This keeps validation + notification + audit centralized.
- **Notifications on commit.** Every admin decision that affects a user (course approve/reject, verification decision, suspend/reactivate) dispatches its notification via `transaction.on_commit` — a rolled-back action never emails.
- **Response envelope.** All admin endpoints use the standard `{success, message, data}` / `{success, message, errors}` envelope and `StandardResultsSetPagination` for lists.
- **Permissions live in `core/`.** No admin permission is defined inside an app directory.

## Where the deep docs are

| Area | Doc |
|---|---|
| Admin console (session auth, devices, user mgmt, audit) | [24-admin-console-auth.md](24-admin-console-auth.md) |
| Course review + lifecycle state machine | [11-course-lifecycle.md](11-course-lifecycle.md) |
| Identity verification | [07-id-verification.md](07-id-verification.md) |
| Institution verification + partner onboarding | [18-partner-institutions.md](18-partner-institutions.md) |
| Platform analytics | [20-analytics-dashboard.md](20-analytics-dashboard.md) |
| Manual API test (console) | [../api-testing/postman-admin-console.md](../api-testing/postman-admin-console.md) |

## What's not built yet (admin-wide)

- **2FA (TOTP)** for admin login — deferred, no library wired in.
- **Step-up re-auth** — `IsRecentlyAuthenticatedAdmin` exists but no endpoint applies it yet.
- **Content moderation, refunds, platform configuration** — future console features on top of `AdminConsoleAPIView`; see `docs/future_implementations/ADMIN_CONSOLE.md`.
- **Platform-wide (all-apps) audit log** — today the audit trail covers only user-management actions in `admin_console`.
