# Platform Admin Console — Features Not Built

**Status:** 🔴 **Planned / unbuilt.** This document lists platform-admin capabilities that do **not** exist in the codebase yet. It is the backlog and scoping reference for the admin surface.

**What exists today (for contrast):** the admin can only do five narrow things, all through `IsPlatformAdmin`-gated `APIView` endpoints — instructor identity verification review, partner-institution verification review, course publish approval/rejection, course-category CRUD, and the raw Django admin site at `/admin/`. There is **no** general-purpose admin console, no user management, no analytics for admins, no moderation queue, no financial administration, and no platform configuration surface. See `CLAUDE.md` → *Permissions* and the "Platform Admin Tasks" summary for the exact endpoints that do exist.

**Login note:** the whole platform (including the current admin endpoints) authenticates with **JWT only** — there is no session-based admin login. Adding one is listed below as its own item.

---

## How to read this list

Each feature below is grouped, given a short plain-language description, and marked with rough scope hints (new models / new app / integrations needed). None of these are started. Where an existing app is the natural home, it is named.

---

## 0. Session-based admin login ✅ **implemented**

**What:** a server-side **session login for admins**, separate from the JWT flow that learners/instructors/institutions use. Delivered as a **JSON API** (the SPA renders the login page) under `/api/v1/admin-console/`.

**Why:** the admin console is a back-office tool used from a browser, not the SPA. Session auth gives CSRF-protected writes, easy "log out everywhere", and short idle timeouts — all awkward to bolt onto the 12-hour JWT access tokens the public API uses.

**Shipped:** new `admin_console/` app. **Login and logout are the shared `POST /api/v1/auth/login/` and `POST /api/v1/auth/logout/`** — for an admin, login opens a Django session + primes CSRF alongside the usual JWT, and logout flushes that session; the console needs no auth endpoints of its own (early dedicated `auth/login/` + `auth/csrf/` + `auth/logout/` were removed as redundant). The console exposes only `GET auth/session/` (who-am-I). `AdminConsoleAPIView` base sets `authentication_classes = [SessionAuthentication, CookieJWTAuthentication, JWTAuthentication]` (session-primary, JWT fallback) + the admin triad; session auth is enabled **per-view only**, never globally, so the JWT API keeps working without CSRF. Idle timeout via `SESSION_SAVE_EVERY_REQUEST` + `SESSION_COOKIE_AGE` (`ADMIN_SESSION_IDLE_TIMEOUT`, default 30 min). Re-auth hook: `IsRecentlyAuthenticatedAdmin` (`core/permissions.py`) checks `session['admin_login_at']` age against `ADMIN_REAUTH_MAX_AGE`. Tests: `admin_console/all_tests/test_admin_session_auth.py`. See `docs/architecture/24-admin-console-auth.md` and `docs/api-testing/postman-admin-console.md`.

**Deferred:** TOTP 2FA (no library installed yet).

---

## 1. Dedicated user-management console 🟡 **partially built**

**What:** a first-class place to administer every account on the platform.

- Search / filter accounts (by email, role/`user_type`, verification state, signup date, activity). ✅ **built** (`GET admin-console/users/`).
- Role editing (change `user_type`, grant/revoke staff/admin). ✅ **built** (`POST users/<id>/role/`).
- Suspend / deactivate / reactivate accounts. ✅ **built** (`POST users/<id>/suspend/` + `/reactivate/` — sets `is_restricted_by_admin` + `is_active`, killing new logins and existing access tokens; suspend also **blacklists outstanding refresh tokens** and both actions **email the user** an account-status notice).
- Dispute & support-ticket handling (view, assign, resolve). 🔴 **not built** (needs `Ticket`/`Dispute` models — own feature).
- Per-user activity & audit logs (who did what, when). 🟡 **audit partially built** — `AdminActionLog` (append-only) records user-mgmt mutations (`GET admin-console/audit/`); a platform-wide audit of *every* admin mutation (§2/§4/§6/§8) is still pending.

**Shipped:** endpoints in `admin_console/all_views/user_views.py`, logic in `admin_console/services/user_admin_service.py`, `AdminActionLog` model (`admin_console/0002`). Suspend enforcement uses the existing `is_active` + `is_restricted_by_admin` flags (both already checked by every login path; `is_active=False` also kills SimpleJWT access tokens). Role switching provisions the target profile via `authentication/services/profile_service.py:ensure_profile_for_type` (now the single source of truth, shared with the create-time signal). All endpoints use the base admin gate (session or JWT + `IsPlatformAdmin`); the step-up `IsRecentlyAuthenticatedAdmin` is available but not applied. Tests: `admin_console/all_tests/test_user_management.py`. See `docs/architecture/24-admin-console-auth.md` → *User management & audit log*.

**Still deferred:** support tickets/disputes and the platform-wide (all-apps) audit log. (Suspension-notification email + reactivation email and refresh-token blacklisting on suspend are now **built**.)

---

## 2. Course management (admin-level)

**What:** platform-wide control over the course catalog beyond the publish approval that already exists.

- Feature / promote courses (curated placements, homepage spots).
- Take down inappropriate content (force-unpublish / hide a course or a single content item).
- Platform-wide pricing rules and refund policies.

**Scope:** new "featured/promoted" flag or ranking model; an admin force-transition path on `NidusCourse` (must still route through `transition_to()` — never set `status` directly); refund policy ties into **Payments Phase 2** (below). Content take-down overlaps with the **moderation queue** (§6).

---

## 3. System-wide analytics (admin) ✅ **built**

**What:** a platform-level analytics dashboard for admins — the counterpart to the existing **institution-scoped** analytics.

- Totals: users, enrollments, revenue, active courses. ✅
- Conversion funnels (signup → enroll → complete → certificate). ✅
- Financial reporting. ✅ **revenue is real** (summed from paid `payments.Order`).

**Shipped:** `admin/` surface inside the existing `analytics/` app — `analytics/services/admin_analytics_service.py`, `analytics/all_views/admin_analytics_views.py`, routes `analytics/admin/{summary,users/trend,enrollments/trend,certificates/trend,revenue/trend,top-courses,funnel}/`, gated `IsPlatformAdmin`. No new models/migrations. Reuses `build_time_series` + the conditional-aggregation strategy, and a new sibling `build_value_series` (sum, not count) for the revenue trend. Tests: `analytics/all_tests/test_admin_analytics.py`.

**Revenue divergence from the partner dashboard:** the old note said revenue was blocked until Payments Phase 2 — that holds for the **partner** dashboard (needs per-institution attribution/payout). At **admin** scope there is no attribution problem (admins see every order), so revenue is computed for real from `Order.objects.filter(status='paid')`. See `CLAUDE.md` → *Analytics Dashboard* → Admin surface and `docs/architecture/20-analytics-dashboard.md`.

---

## 4. Automated approval workflows

**What:** configurable, rule-driven approvals beyond the hand-run verification/course state machines that exist today.

- Auto-approve trusted instructors/institutions on criteria.
- Escalation rules, SLA timers, multi-step routing.
- Bulk approve/reject from a queue.

**Scope:** a rules engine layered on the existing `transition_to()` entry points (identity verification, institution verification, course status). Must not bypass the state machines — it drives them. New config models for rules + a Celery-driven evaluator.

---

## 5. Platform configuration

**What:** a settings surface so non-engineers can tune the platform without a deploy.

- General settings & policies.
- Email templates (currently hard-coded HTML under `emails/` + `_EVENT_TEMPLATE_MAP`).
- Branding (logos, colors, names).
- Commission / revenue-share rates.
- Content guidelines text.

**Scope:** a settings/config model (key-value or typed singletons) with an admin editor. Email-template editing means moving templates from files into editable records (with a safe fallback to the file templates). Commission rates feed **financial management** (§8).

---

## 6. Content moderation queue (manual)

**What:** the human side of moderation — a queue where reported/flagged content lands and an admin acts on it.

- Report/flag intake (from learners/instructors).
- Moderation queue with states (pending → actioned / dismissed).
- Manual actions: hide, warn, take down, escalate.

**Scope:** new `Report`/`ModerationItem` models + queue views; wires into course take-down (§2) and audit log (§1). This is explicitly the **non-AI** portion — automated/AI pre-filtering is a separate future track.

---

## 7. Instructor lifecycle management

**What:** tools to manage instructors as a population, not one verification at a time.

- Onboarding automation (guided setup, checklists, nudges).
- Performance management (surface low/high performers, warnings).
- Segment communication (message a cohort of instructors, e.g. "all unverified", "all with <X rating").

**Scope:** onboarding automation extends the existing expert auto-provision pattern (`authentication/services/expert_service.py`). Performance data comes from the analytics layer. Segment communication is **notification fan-out** (like the unbuilt institution announcements, `INSTITUTION_MESSAGING.md` §8) — not threaded messaging.

---

## 8. Financial & revenue management

**What:** the money back-office. Payments today only records per-order course/webinar checkout (SSLCommerz); there is no platform-level financial administration.

- Revenue management & reporting (platform-wide).
- Payout administration (institution/instructor payouts, wallets).
- Payment-gateway configuration (credentials, providers, modes) via UI.

**Scope:** this is **Payments Phase 2**, already named as unbuilt in `CLAUDE.md` → *Payments* (institution wallet/payout, refunds, analytics `revenue.enabled` flip). New models: `Wallet`, `Payout`, `LedgerEntry`, refund records. Gateway config overlaps with **platform configuration** (§5). Refund policy overlaps with **course management** (§2).

---

## 9. Platform health / performance monitoring

**What:** operational observability for admins/ops.

- Uptime, page/API load times.
- API & database metrics.
- Error tracking.
- Usage analytics (traffic, active sessions).

**Scope:** mostly **infrastructure/tooling**, not app models — integrate an APM/error tracker (e.g. Sentry), metrics export (Prometheus/Grafana), and uptime checks. An in-app admin summary panel can surface a subset. Distinct from product analytics (§3): this is system health, that is business metrics.

---

## Cross-cutting notes

- **Audit log is foundational.** Items §1, §2, §4, §6, §8 all mutate sensitive state and should write to the same append-only audit log. Build it first.
- **Route all state changes through existing entry points.** Course status → `transition_to()`; verifications → their `transition_to()`; enrollments → the enrollment service. Admin power ≠ bypassing invariants.
- **New home app.** Most of this belongs in a new `admin_console/` app, with `analytics/admin/` for §3 and Payments Phase 2 for §8. Keep all permission classes in `core/permissions.py`.
- **Follow the project conventions** in `CLAUDE.md`: `APIView` only, `all_views/` split, the response envelope, the 403-vs-404 access-denied rule, and the try-except pattern.


## Task Summary

Sprint 8: Admin Capabilities

1. Session-Based Admin Login
    - Session authentication
    - Admin login/logout
    - Idle timeout & re-authentication
2. Admin Console Foundation
    - Admin dashboard
    - Navigation & permissions
    - Shared admin components
3. User Management 🟡 partially built
    - User listing & search ✅
    - Role management ✅
    - Suspend/Reactivate accounts ✅
    - Support tickets 🔴 (deferred)
4. Course Management
    - Feature/Promote courses
    - Force publish/unpublish
    - Pricing & refund policy management
5. Platform Analytics ✅ **built**
    - Dashboard overview ✅
    - User, course & revenue analytics ✅ (revenue real, from paid orders)
    - Financial reports ✅
6. Approval Workflows
    - Automated approval rules
    - Bulk approve/reject
    - Escalation workflows
7. Platform Configuration
    - General settings
    - Branding & email templates
    - Commission configuration
8. Content Moderation
    Report & moderation queue
    Hide/Remove content
    Moderation actions
9. Instructor Management
    Onboarding
    Performance monitoring
    Bulk communication
10. Financial Management
    Revenue dashboard
    Wallets & payouts
    Refund management
    Payment gateway settings
11. Platform Monitoring
    System health dashboard
    Performance metrics
    Error monitoring
12. Audit Log
    Audit log model
    Track all admin actions
    Audit log viewer & filters