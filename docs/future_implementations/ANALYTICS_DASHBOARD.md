# Partner Institution Analytics Dashboard

**Status:** ✅ Implemented (Phases 1–4). Revenue + caching (Phase 5) deferred.
**Shipped as a standalone `analytics` app** at `/api/v1/analytics/` (not under `auth/` as the original plan sketched — the data is course/webinar-shaped, so it earned its own app): `analytics/services/analytics_service.py`, `analytics/all_views/analytics_views.py`, `analytics/urls.py`, migration `courses/0017` (index on the courses model), tests `analytics/all_tests/test_analytics.py`. Docs: `docs/architecture/20-analytics-dashboard.md`, `docs/api-testing/postman-analytics.md`, CLAUDE.md subsection.
**URLs:** `partner/summary/`, `partner/enrollments/trend/`, `partner/webinars/trend/`, `partner/certificates/trend/`, `partner/top-courses/` (all under `/api/v1/analytics/`).
**Depends on:** existing `NidusCourse`, `Enrollment`, `Certificate`, `Webinar`, `WebinarRegistration`, `InstructorProfile` roster — all shipped. No new content models.
**SRS:** §7.7 reporting/analytics suite.

---

## 1. Overview

One institution-scoped analytics surface that rolls up the institution's courses, webinars, enrollments, learners, certificates, and expert roster into a single dashboard payload, plus a set of time-series (trend) sub-endpoints.

Every query is scoped to `request.user.partner_institution_profile` reached through:

| Entity | Path to institution |
|---|---|
| `NidusCourse` | `partner_institution=institution` |
| `Enrollment` | `course__partner_institution=institution` |
| `Certificate` | `enrollment__course__partner_institution=institution` |
| `CourseReview` | `course__partner_institution=institution` |
| `Webinar` | `partner_institution=institution` |
| `WebinarRegistration` | `webinar__partner_institution=institution` |
| `InstructorProfile` (roster) | `affiliated_institution=institution, affiliation_status='active'` |

**Never** trust a client-supplied institution id — always resolve from the authenticated user.

### Endpoint surface

> ⚠️ **Original proposal below — superseded at implementation.** The endpoints shipped under a
> standalone `analytics` app at `/api/v1/analytics/partner/...` (not `/api/v1/auth/...`). For the
> as-built URLs, files, and behavior, see the status header above and
> `docs/architecture/20-analytics-dashboard.md`. The rest of this document is the pre-build design and
> is kept for historical rationale.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/auth/partner/analytics/summary/` | Single-shot scalar KPIs (all cards). Default dashboard load. |
| `GET /api/v1/auth/partner/analytics/enrollments/trend/` | Enrollment time series (`?range=monthly\|weekly&months=N`). |
| `GET /api/v1/auth/partner/analytics/webinars/trend/` | Webinar registration time series. |
| `GET /api/v1/auth/partner/analytics/certificates/trend/` | Certificate issuance time series. |
| `GET /api/v1/auth/partner/analytics/top-courses/` | Ranked courses by enrollment / rating / completion. |

**Auth (all):** `IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution` — identical to the other `partner/...` endpoints.

Split rationale: the summary card grid is one cheap fixed-shape response; trends are variable-length series that the frontend fetches lazily per chart. Do **not** stuff trends into the summary payload — a 24-month daily series would bloat the default load.

---

## 2. Functional Requirements — Metrics

Each metric below lists: **what** it represents · **why** useful · **source fields** · **calculation**.

### A. Course metrics

**1. Total courses**
- What: count of every course owned by the institution, any status.
- Why: headline of catalog size / authoring output.
- Fields: `NidusCourse.partner_institution`, `NidusCourse.id`.
- Calc: `NidusCourse.objects.filter(partner_institution=inst).count()`.

**2. Published vs Draft (status breakdown)**
- What: course count per status (`draft`, `institution_review`, `under_review`, `published`, `rejected`, `archived`).
- Why: pipeline health — how many are live vs stuck in review vs abandoned.
- Fields: `NidusCourse.status` (indexed).
- Calc: `NidusCourse.objects.filter(partner_institution=inst).values('status').annotate(n=Count('id'))` → merge into a dict, zero-fill all six statuses. Return `published_courses` and `draft_courses` as named scalars plus the full `status_breakdown` map.

**3. Average course rating (institution-wide)**
- What: mean of `avg_rating` weighted by `review_count` across published courses.
- Why: quality signal for the institution's catalog.
- Fields: `NidusCourse.avg_rating`, `NidusCourse.review_count` (both denormalized, already maintained).
- Calc: `SUM(avg_rating * review_count) / SUM(review_count)` over published courses with `review_count > 0` (guard divide-by-zero → `0`). Also expose `total_reviews = SUM(review_count)`.

### B. Enrollment metrics

**4. Total enrollments**
- What: count of active enrollments across all the institution's courses.
- Why: reach — how many learner-seats the institution serves.
- Fields: `Enrollment.course__partner_institution`, `Enrollment.is_active`.
- Calc: `Enrollment.objects.filter(course__partner_institution=inst, is_active=True).count()`. Also expose `total_enrollments_all_time` (drop `is_active`) so churn is visible.

**5. Enrollment growth**
- What: net new active enrollments in the current period vs the previous period (count + % delta).
- Why: momentum — is demand rising or falling.
- Fields: `Enrollment.created_at`, `Enrollment.course__partner_institution`.
- Calc: count `created_at` in `[now-30d, now)` (current) vs `[now-60d, now-30d)` (previous); `growth_pct = (current - prev) / prev * 100` (guard `prev==0` → `null` or `100` if current>0). Window length driven by `?days=` (default 30).

**6. Active learners**
- What: distinct learners who accessed institution content within a rolling window (default 30 days).
- Why: engagement — enrolled ≠ active; this is the "still showing up" number.
- Fields: `Enrollment.last_accessed_at`, `Enrollment.user_id`.
- Calc: `Enrollment.objects.filter(course__partner_institution=inst, is_active=True, last_accessed_at__gte=now-window).values('user_id').distinct().count()`. Distinct across courses so a learner in three courses counts once.

**7. Course completion rate**
- What: % of active enrollments that reached 100% (`completed_at` not null).
- Why: outcome/efficacy — do learners actually finish.
- Fields: `Enrollment.completed_at`, `Enrollment.is_active`.
- Calc: `completed = filter(..., completed_at__isnull=False).count()`; `rate = completed / total_active * 100` (guard `total==0` → `0`). Single query with conditional aggregation preferred: `Count('id', filter=Q(completed_at__isnull=False))` alongside `Count('id')`.

**8. Average progress**
- What: mean `progress_percent` across active enrollments.
- Why: softer completion signal — captures partial progress that (7) misses.
- Fields: `Enrollment.progress_percent`.
- Calc: `Enrollment.objects.filter(...).aggregate(Avg('progress_percent'))`.

### C. Certificate metrics

**9. Certificates issued**
- What: total certificates issued for the institution's courses.
- Why: verified completions — a stronger outcome metric than progress (certificates are only auto-issued at 100%).
- Fields: `Certificate.enrollment__course__partner_institution`, `Certificate.issued_at`.
- Calc: `Certificate.objects.filter(enrollment__course__partner_institution=inst).count()`. Also expose `certificates_this_month` (`issued_at__gte=start_of_month`).

### D. Webinar metrics

**10. Total webinars**
- What: count of institution webinars, any status.
- Why: live-event output alongside course output.
- Fields: `Webinar.partner_institution`, `Webinar.status`.
- Calc: `Webinar.objects.filter(partner_institution=inst).count()` + per-status breakdown (`draft`/`published`/`archived`).

**11. Upcoming webinars**
- What: published webinars with `scheduled_at` in the future.
- Why: what learners can still register for; institution's forward calendar.
- Fields: `Webinar.status='published'`, `Webinar.scheduled_at`, `Webinar.is_published`.
- Calc: `filter(partner_institution=inst, is_published=True, scheduled_at__gt=now).count()`.

**12. Completed webinars**
- What: published webinars whose `scheduled_at + duration_minutes` is already past.
- Why: delivered-events count; denominator for attendance analysis.
- Fields: `Webinar.scheduled_at`, `Webinar.duration_minutes`.
- Calc: `scheduled_at + (duration_minutes minutes) < now`. In ORM use `ExpressionWrapper(F('scheduled_at') + duration_expr)` or the simpler approximation `scheduled_at__lt=now` if end-time precision is not required (document the choice). Recommend the approximation for v1: `is_published=True, scheduled_at__lt=now`.

**13. Live webinars**
- What: published webinars currently in session (`scheduled_at <= now <= scheduled_at + duration`).
- Why: real-time "happening now" badge.
- Fields: `Webinar.scheduled_at`, `Webinar.duration_minutes`.
- Calc: `scheduled_at <= now AND scheduled_at + duration_minutes >= now`. Compute the window end with an F-expression + `timedelta`; small result set, cheap.

**14. Webinar registrations**
- What: total active registrations across all institution webinars.
- Why: live-event demand, parallel to course enrollments.
- Fields: `WebinarRegistration.webinar__partner_institution`, `WebinarRegistration.is_active`.
- Calc: `WebinarRegistration.objects.filter(webinar__partner_institution=inst, is_active=True).count()`.

**15. Webinar attendance rate**
- What: `attended=True` registrations ÷ active registrations for **completed** webinars.
- Why: show-up quality; registrations over-count intent.
- Fields: `WebinarRegistration.attended`, `WebinarRegistration.is_active`.
- Calc: `Count(filter=Q(attended=True)) / Count(is_active=True) * 100`. **Caveat:** `attended`/`joined_at` are reserved for a later live-day join flow (per `registration_models.py`), so this reads `0%` until that flow ships. Include the metric but flag it `"attendance_tracking_enabled": false` in the payload until the join flow lands.

**16. Webinar capacity fill**
- What: for capacity-limited webinars, active registrations ÷ `max_capacity`.
- Why: are events selling out or under-filled.
- Fields: `Webinar.max_capacity`, `WebinarRegistration.is_active`.
- Calc: per webinar `registrations / max_capacity` where `max_capacity is not null`; report the average fill across capped webinars. Optional for v1.

### E. Expert / roster metrics

**17. Expert count**
- What: active affiliated experts on the roster.
- Why: institution's teaching capacity.
- Fields: `InstructorProfile.affiliated_institution`, `InstructorProfile.affiliation_status`.
- Calc: `InstructorProfile.objects.filter(affiliated_institution=inst, affiliation_status='active').count()`. Optionally `experts_total` (include `removed`) for churn.

**18. Institution engagement (composite index)**
- What: a single 0–100 health score blending completion rate, active-learner ratio, avg rating, and webinar attendance.
- Why: one-glance overall-health number for the dashboard hero card.
- Fields: derived from metrics 3, 6, 7, 15.
- Calc: weighted average of normalized components, e.g. `0.35*completion_rate + 0.30*active_ratio + 0.20*(avg_rating/5*100) + 0.15*attendance_rate`. **Document the exact weights in code.** Keep it explainable — return the component values alongside the composite so the frontend can show the breakdown. Ship this **last** (needs the others stable first).

### F. Revenue metrics — DEFERRED

**19. Revenue**
- What: money earned from paid courses/webinars.
- Why: financial reporting for the institution.
- Fields: `NidusCourse.price`, `Webinar.price`, `Enrollment.enrollment_type='paid'`.
- **Status: NOT truly supported.** There is **no** payment/order/transaction model in the codebase (grep for `Payment|Order|Transaction|Invoice` → none). `price` and `enrollment_type='paid'` exist but no money actually changes hands or is recorded.
- Interim option (label clearly as **estimated gross**, not booked revenue): `SUM(course.price)` over enrollments with `enrollment_type='paid'`, plus `SUM(webinar.price * active_registrations)`. This ignores refunds, discounts, coupons, and payment-gateway fees.
- **Recommendation:** exclude from v1 or gate behind a `revenue_tracking_enabled` flag returning `null`. Real revenue needs a payments app first (a separate, larger feature).

### G. Trend metrics (time series)

**20. Monthly trends**
- What: per-month buckets for the last N months (default 12) of enrollments, registrations, certificates.
- Why: seasonality + long-range trajectory for line charts.
- Fields: `created_at` (enrollments/registrations), `issued_at` (certificates).
- Calc: `TruncMonth('created_at')` → `.values('month').annotate(n=Count('id')).order_by('month')`. **Zero-fill missing months in Python** so the series is contiguous (SQL only returns months with rows).

**21. Weekly trends**
- What: per-week buckets for the last N weeks (default 12).
- Why: finer granularity for recent momentum.
- Fields: same as monthly.
- Calc: `TruncWeek(...)`; same zero-fill.

Both driven by one shared helper `build_time_series(queryset, date_field, granularity, periods)` → returns `[{period: '2026-06', count: 42}, ...]` contiguous.

---

## 3. Summary Payload Shape

```json
{
  "success": true,
  "data": {
    "courses": {
      "total": 40,
      "published": 28,
      "draft": 7,
      "status_breakdown": {"draft": 7, "institution_review": 2, "under_review": 1,
                            "published": 28, "rejected": 1, "archived": 1},
      "avg_rating": 4.42,
      "total_reviews": 1310
    },
    "enrollments": {
      "active": 5120,
      "all_time": 5600,
      "growth": {"current": 240, "previous": 180, "growth_pct": 33.3, "window_days": 30},
      "active_learners": 3110,
      "completion_rate": 61.5,
      "avg_progress": 58.2
    },
    "certificates": {"total": 1980, "this_month": 210},
    "webinars": {
      "total": 22, "published": 15, "draft": 6, "archived": 1,
      "upcoming": 5, "live": 0, "completed": 10,
      "registrations": 4300,
      "attendance_rate": 0.0, "attendance_tracking_enabled": false
    },
    "roster": {"experts_active": 18, "experts_total": 20},
    "revenue": {"enabled": false, "estimated_gross": null},
    "engagement_score": {"composite": 72.4,
      "components": {"completion": 61.5, "active_ratio": 60.7, "rating": 88.4, "attendance": 0.0}}
  }
}
```

Trend endpoints return:

```json
{"success": true, "data": {
  "granularity": "monthly", "periods": 12,
  "series": [{"period": "2025-08", "count": 120}, {"period": "2025-09", "count": 145}]
}}
```

---

## 4. Query Strategy & Performance

- **Summary = a fixed, small number of aggregate queries** (roughly: 1 course-status group, 1 course-rating aggregate, 2–3 enrollment aggregates, 1 certificate count, 1–2 webinar aggregates, 1 registration count, 1 roster count ≈ **~10 queries total, independent of data volume**). No per-row Python loops, no N+1.
- Use **conditional aggregation** (`Count('id', filter=Q(...))`, `Avg(...)`) to fold multiple counts into one query where they share a base queryset (e.g. active total + completed count + avg progress over enrollments = one `.aggregate()`).
- All the filter paths hit existing indexes: `idx_ncourse_creator_status` won't match `partner_institution`, so **add an index** `NidusCourse(partner_institution, status)` and `Webinar` already has `idx_webinar_inst_status`. Enrollment reaches institution via a join on `course` — the existing `idx_enroll_course_active` covers the `course_id, is_active` leg.
- **Caching (optional, phase 4):** summary is read-heavy and tolerant of staleness. Cache per-institution for 5–15 min with a key like `analytics:summary:{institution_id}`; invalidate lazily by TTL (exact invalidation not worth it for a dashboard).
- **Time zones:** `TruncMonth`/`TruncWeek` bucket in the DB's timezone. Pass `tzinfo=timezone.get_current_timezone()` to the Trunc functions so buckets align to the platform tz, not UTC midnight.

---

## 5. Files to Change

1. **`courses/services/analytics_service.py`** — **new.** Home for all aggregation (the heavy entities live in `courses`/`webinars`, and cross-app reads are fine from a service).
   - `institution_summary(institution) -> dict` — the full card payload.
   - `enrollment_trend(institution, granularity, periods) -> list[dict]`.
   - `webinar_registration_trend(...)`, `certificate_trend(...)`.
   - `top_courses(institution, sort, limit) -> list[dict]`.
   - `build_time_series(qs, date_field, granularity, periods)` — shared zero-filling helper.
   - Lazy-import webinar models inside functions to avoid `courses → webinars` import-order surprises if needed.

2. **`authentication/all_views/partner_views.py`** — **new views** (mirror `InstitutionExpertActivityView` from the activity-rollup spec):
   - `InstitutionAnalyticsSummaryView`, `InstitutionEnrollmentTrendView`, `InstitutionWebinarTrendView`, `InstitutionCertificateTrendView`, `InstitutionTopCoursesView`.
   - `permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]`.
   - **Lazy-import** the service inside each method (`from courses.services.analytics_service import ...`) to avoid an `authentication → courses` circular import at module load.
   - Resolve `institution = request.user.partner_institution_profile`; parse/validate query params; wrap in the standard `{'success': True, 'data': {...}}` envelope.
   - `try/except` around the aggregation → log + 500 on unexpected failure (per project try-except pattern). Param validation errors → 400.

3. **`authentication/urls.py`** — register the five routes under `partner/analytics/...`. Place literal paths before any `<int:...>` route (no collision here, but keep the ordering convention).

4. **`authentication/serializers.py`** — **optional.** Lean approach: return plain server-computed dicts from the service (nothing to validate on output). Add small input serializers only if param parsing (`range`, `granularity`, `periods`, `days`, `limit`) gets non-trivial; otherwise validate inline with sane defaults + clamps (e.g. `periods` clamped to `[1, 24]`, `limit` to `[1, 50]`).

5. **`courses/migrations/xxxx_*.py`** — **one migration** adding `models.Index(fields=['partner_institution', 'status'], name='idx_ncourse_inst_status')` to `NidusCourse.Meta.indexes`. (Webinar already has its institution index; Enrollment/Certificate reach institution via joins that use existing indexes.) Update the model `Meta` in the same change.

6. **`CLAUDE.md`** — document under a new *Partner Institution: Analytics Dashboard* subsection: the five endpoints, the service module, the "revenue is estimated/deferred" caveat, and the "attendance reads 0 until join-flow ships" caveat.

7. **`courses/all_tests/test_analytics.py`** (or `authentication/tests/...`) — **new** test suite.

---

## 6. Edge Cases & Rules

- **403 vs 404:** these endpoints take **no resource id** in the path (institution derived from the token), so the only failure is permission → `IsVerifiedPartnerInstitution` yields **403**. The `top-courses`/trend endpoints that accept `?course_id=` must return **404** for a `course_id` not owned by the institution (numeric id → 404, per project policy), never 403.
- **Cross-institution safety:** every query filtered by `partner_institution=institution` resolved from `request.user` — never from a client param.
- **Empty institution:** a brand-new institution with zero courses must return a fully-formed payload of zeros/nulls (never 500, never missing keys). Zero-fill every scalar and every status bucket.
- **Divide-by-zero:** completion rate, growth %, avg rating, attendance, capacity fill all guard empty denominators → `0` or `null` (pick one convention and document it; recommend `0` for rates, `null` for "not computable" growth).
- **Removed experts / archived courses / cancelled enrollments:** decide inclusion per metric and document. Default: totals count all-time; "active"-suffixed metrics filter `is_active`/`affiliation_status='active'`.
- **Null-author / legacy rows:** not relevant here (analytics counts rows, not authorship) — but note if a course's `partner_institution` was `SET_NULL` (institution deleted), those rows silently drop out; that's correct.
- **Revenue:** return `{"enabled": false, "estimated_gross": null}` in v1. Do not surface fabricated money numbers.
- **Attendance:** return the field but with `attendance_tracking_enabled: false` until the webinar live-day join flow ships.

---

## 7. Build Order (suggested)

1. **Phase 1 — Summary core.** `analytics_service.institution_summary` covering courses (1–3), enrollments (4–8), certificates (9), roster (17). View + URL + zeros-for-empty test. Add the `NidusCourse` institution index migration.
2. **Phase 2 — Webinar metrics.** Extend summary with 10–14 (+16 optional). Attendance (15) as a flagged-off field.
3. **Phase 3 — Trends.** `build_time_series` helper + the three trend endpoints (20, 21). Zero-fill + tz-aware Trunc.
4. **Phase 4 — Top courses + engagement score.** `top_courses` endpoint; composite `engagement_score` (18) once its inputs are stable.
5. **Phase 5 (optional) — Caching** per-institution summary (TTL 5–15 min) if load warrants.
6. **Revenue** — only after a real payments/orders app exists. Out of scope here.

---

## 8. Future Extensions

- Per-course drill-down nested under each metric (enrollments/completion/rating per course).
- Per-department analytics (join `InstructorProfile.department` → course authorship) — ties to the department feature.
- Per-expert performance rollup — overlaps with `EXPERT_CONTENT_ACTIVITY_ROLLUP.md`; share the roster query.
- Date-range filter (`?since=&until=`) across all metrics for custom reporting periods.
- CSV / PDF export of the summary for offline reporting.
- Real revenue once a payments app lands (orders, refunds, coupons, gateway fees).
