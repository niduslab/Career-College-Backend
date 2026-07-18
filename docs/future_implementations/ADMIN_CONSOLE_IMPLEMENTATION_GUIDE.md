# Admin Console — Implementation Guide (What's Left & How to Build It)

**Companion to** `ADMIN_CONSOLE.md` (the backlog). This doc turns that backlog into concrete build steps: for each unbuilt feature it lists **what to do**, **new files to create**, and **existing files affected**. Written to be followed in order — the foundation and audit log come first because everything else leans on them.

**Read first:** `CLAUDE.md` → *Admin Console*, *Permissions*, *Response Format*, *Try-Except Pattern*, *403-vs-404 policy*, *View File Convention*. Every new endpoint below must obey those conventions (`APIView` only, `all_views/` split, subclass `AdminConsoleAPIView`, standard envelope).

---

## Status snapshot (from `ADMIN_CONSOLE.md`)

| # | Feature | State |
|---|---------|-------|
| 0 | Session-based admin login | ✅ done |
| 1 | User management | 🟡 partial — search/role/suspend **+ suspend-email + token-blacklist done**; tickets and platform-wide audit left |
| — | **Admin console foundation** (dashboard, nav, shared components) | 🔴 **current sprint** (`feature/8.2/admin_console_foundation`) |
| 2 | Course management (admin) | 🔴 |
| 3 | System-wide analytics | ✅ built (revenue real, from paid orders) |
| 4 | Automated approval workflows | 🔴 |
| 5 | Platform configuration | 🔴 |
| 6 | Content moderation queue | 🔴 |
| 7 | Instructor lifecycle management | 🔴 |
| 8 | Financial & revenue management (Payments Phase 2) | 🔴 |
| 9 | Platform health monitoring | 🔴 (mostly infra/tooling) |
| 12 | Platform-wide audit log | 🔴 (foundational — build early) |

---

## Recommended build order

1. **Admin console foundation** (current sprint) — dashboard + nav manifest + shared base pieces. Cheap, unblocks the UI.
2. **Platform-wide audit log** — §1/§2/§4/§6/§8 all write to it. Build the shared writer before those features.
3. **Course management (admin)** — small, high value, reuses existing `transition_to()`.
4. **System-wide analytics** — reuses `analytics/` helpers, no new models.
5. **Content moderation queue** — depends on audit + course take-down from §3.
6. **Platform configuration** — unblocks branding, email templates, commission rates.
7. **Automated approval workflows** — sits on top of existing state machines.
8. **Instructor lifecycle** — reuses expert-provision + analytics + notification fan-out.
9. **Financial management (Payments Phase 2)** — biggest; needs config (§6) for commission.
10. **Platform health monitoring** — infra track (Sentry/Prometheus), do independently.

---

## 0. Admin Console Foundation (current sprint — 8.2)

**What:** a landing dashboard + a navigation/permission manifest the SPA reads to render the back-office shell, plus any shared serializer/view helpers future admin endpoints reuse. No new domain models — pure aggregation + a config endpoint.

**Do:**
- `GET admin-console/dashboard/` — one-shot KPI cards for admins (platform totals: users by type, courses by status, pending verification queues, pending course-review count, recent admin actions). Aggregate over existing models; do **not** add models.
- `GET admin-console/navigation/` (or bake into `auth/session/`) — return the menu manifest + which sections this admin may see (all admins are equal today, but return the shape so the SPA is future-proof for scoped roles).
- Extract any shared list/response helper into the app so §2–§8 endpoints reuse it (mirrors `analytics/` `_BaseTrendView`).

**New files:**
- `admin_console/all_views/dashboard_views.py` — `AdminDashboardView(AdminConsoleAPIView)`.
- `admin_console/services/dashboard_service.py` — `platform_overview()` (fixed-count aggregate queries).
- `admin_console/all_serializers/dashboard_serializers.py` — response shape.
- `admin_console/all_tests/test_dashboard.py`.

**Affected files:**
- `admin_console/urls.py` — add `dashboard/`, `navigation/` routes.
- `admin_console/views.py`, `admin_console/serializers.py` — re-export new symbols.
- `docs/architecture/24-admin-console-auth.md` — add a "Dashboard & foundation" section.
- `docs/api-testing/postman-admin-console.md` — add the new calls.

**Reuse:** `AdminConsoleAPIView` base (`all_views/base.py`), `IsPlatformAdmin`. No new permission class.

---

## 12. Platform-wide Audit Log (build before §2/§4/§6/§8)

**What:** today `AdminActionLog` (`admin_console/all_models/user_admin_models.py`) only logs user-management mutations, and its `Action` choices are `SUSPEND/REACTIVATE/ROLE_CHANGE`. Make it the single append-only log every sensitive admin mutation writes to.

**Do:**
- Widen `AdminActionLog.Action` TextChoices to cover course take-down, feature/promote, config change, moderation action, approval-rule change, payout, refund, etc. (or drop the enum constraint and store a namespaced string like `course.force_unpublish`).
- Generalize the target: today it FKs `target_user`. Add a **generic target** (`target_content_type` + `target_object_id` GenericFK) plus keep the snapshotted `actor_email`/`target_email` pattern so attribution survives deletion. Keep `metadata` JSON for before/after.
- Keep `log_admin_action()` (`admin_console/services/user_admin_service.py:66`) as the writer but move it to a shared module so every app can import it without importing user-management logic.

**New files:**
- `admin_console/services/audit_service.py` — move/extract `log_admin_action(actor, action, *, target=None, reason='', metadata=None)`; add `search_audit(...)`.
- Migration `admin_console/000X` — widen `Action`, add generic-target columns + index.

**Affected files:**
- `admin_console/all_models/user_admin_models.py` — model change.
- `admin_console/services/user_admin_service.py` — import writer from `audit_service`, keep back-compat.
- `admin_console/all_views/user_views.py` — `AdminAuditLogListView` gains new filters (`?action`, `?target_type`).
- `admin_console/all_serializers/user_serializers.py` — `AdminActionLogSerializer` renders generic target.
- Every feature below calls `audit_service.log_admin_action(...)` inside its mutation transaction.

**Convention:** write the audit row **in the same `transaction.atomic()`** as the mutation, take `select_for_update` on the target — exactly as user-management already does.

---

## 1. User Management — remaining pieces

Search/role/suspend already shipped. **1a + 1b now done** (see below, kept for reference). Left: **1c** (support tickets/disputes) and the platform-wide audit log (§12).

### 1a. Suspension-notification email ✅ done
Greenfield notification wiring — needs the **4 edits** (`CLAUDE.md` → notifications):
1. `notifications/models.py` — add `NotificationEventType.ACCOUNT_SUSPENDED` (+ `REACTIVATED`).
2. `notifications/services/builders.py` — add `_account_suspended` builder.
3. `notifications/services/preference_service.py` — add to `EVENT_TO_CATEGORY`.
4. `notifications/email_utils.py` — add to `_EVENT_TEMPLATE_MAP` + create `templates/notifications/emails/account_suspended.html|.txt`.

**Affected:** `admin_console/services/user_admin_service.py` (`suspend_user`/`reactivate_user`) — `dispatch()` on `transaction.on_commit`.

### 1b. Refresh-token blacklist on suspend ✅ done
**Do:** on suspend, blacklist the target's outstanding refresh tokens (SimpleJWT `OutstandingToken`/`BlacklistedToken`) so they can't refresh a new access token. Access tokens already die because `is_active=False`.

**Affected:** `admin_console/services/user_admin_service.py:131` (`suspend_user`) — iterate `OutstandingToken.objects.filter(user=target)` → blacklist. Add test in `test_user_management.py`.

### 1c. Support tickets & disputes (own feature)
**New models:** `Ticket`, `Dispute` (status machine `open → assigned → resolved/dismissed`, FK to `User`, optional FK to course/order).

**New files:** likely a **new `support/` app** (or `admin_console/all_models/ticket_models.py` if kept in-console) — models, service (`ticket_service.py` with a `transition_to`-style state machine), `all_views/ticket_views.py`, serializers, urls, tests.

**Affected:** `career_college_backend/settings.py` (add app + urls if new app), `admin_console/urls.py` or new `support/urls.py`. Writes to the shared audit log.

---

## 2. Course Management (admin-level)

**Do:**
- **Feature/promote:** add ranking fields to `NidusCourse` (none exist today) — e.g. `is_featured` (bool) + `feature_rank` (nullable int) + `featured_at`. Admin endpoints to set/clear. Catalog sort reads them.
- **Force publish / unpublish (take-down):** admin transition path — **must route through `NidusCourse.transition_to()`**, never set `status` directly. May need a new transition edge (e.g. `published → archived` by admin already exists; add force-unpublish reason).
- **Pricing / refund policy:** platform-wide rules → belongs to **Platform Configuration (§5)** + **Financial (§8)**; stub only.

**New files:**
- `admin_console/all_views/course_admin_views.py` — `AdminCourseListView`, `AdminCourseFeatureView`, `AdminCourseTakedownView`.
- `admin_console/services/course_admin_service.py` — `set_featured()`, `force_transition()` (wraps `transition_to`, writes audit).
- Migration `courses/00XX` — add `is_featured`/`feature_rank`/`featured_at` + index (mirror `idx_ncourse_inst_status`).
- Tests `admin_console/all_tests/test_course_admin.py`.

**Affected files:**
- `courses/all_models/course_models.py` — new fields on `NidusCourse` (near `avg_rating:227`).
- `courses/all_serializers/` catalog serializer — expose `is_featured`; catalog list view — allow `?sort=featured`.
- `admin_console/urls.py`, `views.py`, `serializers.py`.
- Audit log (`audit_service.log_admin_action`).

**Rule:** force-transition still calls `transition_to(new_status, reviewer=admin)` — admin power ≠ bypassing the state machine.

---

## 3. System-Wide Analytics (admin) ✅ **built**

**What:** platform-level counterpart to the institution dashboard, in the `admin/` URL segment beside `partner/`.

**Shipped endpoints:** `GET analytics/admin/{summary,users/trend,enrollments/trend,certificates/trend,revenue/trend,top-courses,funnel}/`. **No new models** — pure aggregation.

**Files created:**
- `analytics/all_views/admin_analytics_views.py` — views gated `IsPlatformAdmin` (plain `APIView`, not `AdminConsoleAPIView`; admin SPA's JWT cookie reaches it).
- `analytics/services/admin_analytics_service.py` — reuses `build_time_series`, `_normalize_trend_params`, `_pct`, `_COURSE_STATUSES`, and the new `build_value_series` (sum, not count) for revenue; platform scope = **no institution filter**. Shares fn names with the partner service → imported by full module path.
- Tests `analytics/all_tests/test_admin_analytics.py`.

**Files touched (additive):** `analytics/services/analytics_service.py` (+`build_value_series`), `analytics/all_views/__init__.py`, `analytics/views.py`, `analytics/urls.py`.

**Revenue is REAL (not blocked).** The original "blocked until Payments Phase 2" note applied when no payments model existed and to per-institution attribution. At **admin** scope revenue is summed from `Order.objects.filter(status='paid')` with no attribution problem, so `revenue.enabled=True`. Only the **partner** dashboard keeps `enabled: False`.

---

## 4. Automated Approval Workflows

**What:** rule-driven approvals that **drive** the existing state machines, never bypass them.

**Do:**
- New config models: `ApprovalRule` (target type: identity/institution/course; criteria JSON; action: auto-approve/escalate; enabled), optional `ApprovalEscalation` (SLA timer).
- A Celery-driven evaluator that, on submission events, checks rules and calls the relevant `transition_to()`.
- Bulk approve/reject endpoint over a queue.

**New files:**
- `admin_console/all_models/approval_models.py` — `ApprovalRule`, `ApprovalEscalation`.
- `admin_console/services/approval_service.py` — `evaluate_rules(target)`, `bulk_decide(...)`.
- `admin_console/tasks.py` — new file: `evaluate_approvals_task`, SLA beat task.
- `admin_console/all_views/approval_views.py` — rule CRUD + bulk-decide.
- Tests.

**Affected files:**
- `id_verification/models.py` — call the evaluator on `transition_to('submitted')` (or hook via signal / dispatch on-commit). Entry points: `IdentityVerification.transition_to:193`, `InstitutionVerification.transition_to:405`.
- `courses/all_models/course_models.py` — course submission hook (`transition_to:306`).
- `career_college_backend/settings.py` — Celery beat schedule for the SLA task.
- `admin_console/urls.py`, `views.py`. Writes audit.

**Rule:** the evaluator calls `transition_to()` — it does not write `status`/verification state directly.

---

## 5. Platform Configuration

**What:** editable settings so non-engineers tune the platform without a deploy.

**Do:**
- A typed key-value config model (`PlatformSetting`: key, value JSON, category) or typed singletons. Cached; invalidated on write.
- Editable **email templates**: move from files (`notifications/templates/.../emails/*.html` + `_EVENT_TEMPLATE_MAP`) into DB records with a **safe fallback to the file template** when no record exists.
- Branding (logos/colors/names), content-guidelines text, commission rate (feeds §8).

**New files:**
- `admin_console/all_models/config_models.py` — `PlatformSetting`, `EmailTemplateOverride`.
- `admin_console/services/config_service.py` — `get_setting(key, default)`, `set_setting(...)` (audited), cache layer.
- `admin_console/all_views/config_views.py` — list/get/patch settings, email-template editor.
- Tests.

**Affected files:**
- `notifications/email_utils.py` — `render_notification_email:34` checks `EmailTemplateOverride` first, falls back to `_EVENT_TEMPLATE_MAP:7`.
- `admin_console/urls.py`, `views.py`, `serializers.py`.
- Anywhere hard-coded branding/commission is read (introduce `config_service.get_setting`).

---

## 6. Content Moderation Queue (manual, non-AI)

**Do:**
- New models: `Report` (reporter, target GenericFK to course/content/review/user, reason, created), `ModerationItem` (state `pending → actioned/dismissed`, assigned admin, resolution).
- Report intake endpoint (learner/instructor-facing) + admin queue views (list/act).
- Actions: hide, warn, take down (calls §2 force-unpublish), escalate.

**New files:**
- `admin_console/all_models/moderation_models.py` — `Report`, `ModerationItem`.
- `admin_console/services/moderation_service.py` — intake + `resolve()` state machine (`ModerationError(message, http_status)` pattern).
- `admin_console/all_views/moderation_views.py` — intake view (non-admin gate) + admin queue views (`AdminConsoleAPIView`).
- Tests.

**Affected files:**
- `courses/` — content models get a `GenericRelation` to `Report` if you want cascade cleanup (optional).
- `core/permissions.py` — if intake needs a new gate (e.g. `IsEnrolledOrInstructor`), add it here (permissions live in `core/` only).
- `admin_console/urls.py`, `views.py`. Take-down reuses `course_admin_service.force_transition`. Writes audit.

---

## 7. Instructor Lifecycle Management

**Do:**
- **Onboarding automation:** extends the expert auto-provision pattern (`authentication/services/expert_service.py`) — checklists/nudges via scheduled notifications.
- **Performance management:** read from the analytics layer (reuse `expert_performance_service.py` / new admin analytics §3) to surface low/high performers.
- **Segment communication:** notification **fan-out** (not threaded messaging) — pick a cohort (e.g. all unverified instructors) and dispatch a notification to each.

**New files:**
- `admin_console/all_views/instructor_admin_views.py` — segment list + broadcast endpoint.
- `admin_console/services/instructor_admin_service.py` — `segment_query(filters)`, `broadcast_to_segment(...)`.
- New notification event for broadcasts (4-edit wiring, §1a pattern).

**Affected files:**
- `authentication/services/expert_service.py` — onboarding nudge hooks.
- `notifications/` — the 4 wiring points for a `INSTRUCTOR_BROADCAST` event.
- `analytics/services/` — performance queries (or reuse admin analytics).
- `admin_console/urls.py`, `views.py`.

---

## 8. Financial & Revenue Management (Payments Phase 2)

**Biggest.** Payments today has **only** the `Order` model (`payments/all_models/order_models.py`) — confirmed **no** `Wallet`/`Payout`/`Ledger`/`Refund` model exists. `requires_refund` is only a flag in `Order.gateway_payload`.

**Do:**
- New models: `Wallet` (owner = institution/instructor), `LedgerEntry` (double-entry: order → platform + owner split by commission), `Payout` (status machine), `Refund` (links `Order`, gateway refund call).
- Populate the ledger on `finalize_payment` (`order_service.py:199`) success.
- Platform-wide revenue reporting → flips `analytics` `revenue.enabled` to `True` (§3).
- Refund flow (admin-initiated) → SSLCommerz refund API + ledger reversal.
- Payout administration endpoints.
- Gateway config via UI overlaps with §5.

**New files:**
- `payments/all_models/wallet_models.py` — `Wallet`, `LedgerEntry`, `Payout`, `Refund`.
- `payments/services/ledger_service.py` — `record_sale(order)`, commission split (reads `config_service` commission rate from §5).
- `payments/services/payout_service.py`, `payments/services/refund_service.py`.
- `payments/all_views/admin_finance_views.py` — payout/refund/revenue admin endpoints (gated `IsPlatformAdmin`).
- Migrations, tests.

**Affected files:**
- `payments/services/order_service.py` — `finalize_payment:199` / `_grant_access:304` → also `ledger_service.record_sale` on commit.
- `analytics/services/*` — flip `revenue.enabled`, wire real numbers.
- `admin_console/all_views/course_admin_views.py` — refund policy links here.
- `career_college_backend/settings.py` — refund/gateway config.
- **Rule:** never `logger.info` in payments (warning/error/critical only). Audit every payout/refund.

---

## 9. Platform Health / Performance Monitoring

**Mostly infra, not app models.** Do independently of the feature track.

**Do:**
- Integrate an error tracker (Sentry) — `sentry-sdk` in `requirements.txt`, init in `settings.py`.
- Metrics export (Prometheus/`django-prometheus`) + Grafana; uptime checks (external).
- An in-app admin summary panel surfacing a subset (queue depths, Celery health, recent errors count).

**New files:**
- `admin_console/all_views/health_views.py` — `AdminHealthView` (Celery ping, DB check, queue depth).
- `admin_console/services/health_service.py`.

**Affected files:**
- `requirements.txt` — `sentry-sdk`, `django-prometheus`.
- `career_college_backend/settings.py` — Sentry init, Prometheus middleware/apps.
- `career_college_backend/urls.py` — `/metrics` mount (if Prometheus).

**Distinct from §3:** this is system health (uptime, errors), §3 is business metrics (users, revenue).

---

## Cross-cutting rules (apply to every feature above)

- **Subclass `AdminConsoleAPIView`** for every admin-console endpoint (`admin_console/all_views/base.py`) — session-primary auth + `IsPlatformAdmin` + idle-timeout touch. Never re-enable session auth globally.
- **Permissions live in `core/permissions.py` only.** `IsRecentlyAuthenticatedAdmin` already exists for step-up re-auth (not yet applied) — apply it to the sensitive mutations (§2 take-down, §4 rule changes, §8 payouts/refunds).
- **Route all state changes through existing entry points** — `NidusCourse.transition_to()`, verification `transition_to()`, `enroll_learner`. Admin power drives them, never bypasses.
- **Every sensitive mutation writes the shared audit log** (§12) in the same transaction, `select_for_update` on the target, throttled per-admin (`ADMIN_ACTION_RATE_LIMIT`).
- **Response envelope + 403-vs-404 rule + try-except pattern** per `CLAUDE.md`. Numeric-id no-access → 404; slug no-access → 403.
- **Docs:** for each feature add/extend a numbered doc in `docs/architecture/` and a `docs/api-testing/postman-*.md` walkthrough, and update `CLAUDE.md` → *Admin Console*.

---

## Quick file-impact matrix

| Feature | New app? | New models | Key existing files touched |
|---|---|---|---|
| Foundation | no | none | `admin_console/{urls,views,serializers}.py`, docs |
| Audit log (§12) | no | widen `AdminActionLog` (generic target) | `user_admin_service.py`, `user_views.py` |
| User mgmt leftovers (§1) | maybe (`support/`) | `Ticket`,`Dispute` | `user_admin_service.py`, `notifications/*` (4 edits), SimpleJWT blacklist |
| Course mgmt (§2) | no | fields on `NidusCourse` | `course_models.py`, catalog serializer/view, `transition_to` |
| Analytics (§3) | no | none | `analytics/{urls,views}.py`, reuse `analytics_service.build_time_series` |
| Approvals (§4) | no | `ApprovalRule`,`ApprovalEscalation` | verification + course `transition_to`, new `tasks.py`, settings beat |
| Config (§5) | no | `PlatformSetting`,`EmailTemplateOverride` | `notifications/email_utils.py` |
| Moderation (§6) | no | `Report`,`ModerationItem` | `core/permissions.py`, course take-down |
| Instructor lifecycle (§7) | no | none | `expert_service.py`, `notifications/*`, analytics |
| Financial (§8) | no (extends `payments/`) | `Wallet`,`LedgerEntry`,`Payout`,`Refund` | `order_service.finalize_payment`, `analytics revenue`, settings |
| Health (§9) | no | none | `requirements.txt`, `settings.py`, `urls.py` |
