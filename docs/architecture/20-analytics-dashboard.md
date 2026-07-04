# 20 — Partner Institution Analytics Dashboard

A read-only reporting surface that rolls up a partner institution's own courses, enrollments,
learners, certificates, webinars, and expert roster into dashboard KPIs plus time-series trends.
It is a **standalone app** (`analytics/`) that owns **no models** — it aggregates over existing tables
in `courses`, `webinars`, and `authentication`. Mounted at `/api/v1/analytics/`. Source requirement:
SRS §7.7 reporting suite.

**App:** `analytics/` (mirrors the project app layout: `all_views/`, `services/`, `all_tests/`, thin `views.py` / `urls.py`)
**Aggregation:** `analytics/services/analytics_service.py`
**Views:** `analytics/all_views/analytics_views.py`
**Routes:** `analytics/urls.py` (`app_name='analytics'`), included at `/api/v1/analytics/`
**Tests:** `analytics/all_tests/test_analytics.py`
**Plan:** `docs/future_implementations/ANALYTICS_DASHBOARD.md`

---

## Design decisions

**Read-only, no new tables.** Every number already exists in `NidusCourse`, `Enrollment`,
`Certificate`, `Webinar`, `WebinarRegistration`, `InstructorProfile`. The feature is pure aggregation,
so it ships as one app with a service module + views + one supporting index (on the `courses` model) —
no schema growth beyond the index, and the `analytics` app itself has no migrations.

**Institution derived from the token, never the client.** Each view resolves
`request.user.partner_institution_profile` and passes it to the service; every query filters on it.
There is no institution id in any URL or query param, so cross-institution data cannot leak.

**Its own app, not folded into `authentication` or `courses`.** The partner-console endpoints for
experts/departments live under `authentication` because they manage auth-domain objects (users,
profiles). Analytics is course/webinar-shaped data, so bundling it under `/api/v1/auth/` read wrong.
It is not folded into `courses` either, because it spans `courses` **and** `webinars` **and**
`authentication` — no single existing app owns it. A dedicated `analytics` app is the honest home, and
it depends on the others one-directionally (nothing imports `analytics`), so there is no import cycle
and views import the service normally. The `partner/` URL segment scopes the current endpoints to the
institution audience, leaving room for an `admin/` analytics surface later under the same app.

**Summary vs trends split.** The summary is one fixed-shape payload the dashboard loads once; trends
are variable-length series each chart fetches lazily. Bundling a 24-month series into the summary would
bloat the default load, so they are separate endpoints.

---

## Endpoints

All gated `IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution`.

All under the base `/api/v1/analytics/`.

| Endpoint | View | Purpose |
|---|---|---|
| `GET partner/summary/` | `InstitutionAnalyticsSummaryView` | All KPI cards in one payload. |
| `GET partner/enrollments/trend/` | `InstitutionEnrollmentTrendView` | Enrollment series. |
| `GET partner/webinars/trend/` | `InstitutionWebinarTrendView` | Webinar-registration series. |
| `GET partner/certificates/trend/` | `InstitutionCertificateTrendView` | Certificate-issuance series. |
| `GET partner/top-courses/` | `InstitutionTopCoursesView` | Ranked courses. |
| `GET partner/experts/performance/` | `InstitutionExpertPerformanceView` | Per-expert outcome metrics (whole roster). |
| `GET partner/experts/<expert_id>/performance/` | `InstitutionExpertPerformanceDetailView` | One expert (id → 404 if not an active affiliate). |

## Expert performance (`expert_performance_service.py`)

Drills below the institution-wide KPIs to **per-expert** outcomes: courses credited,
content authored (sections/lectures/quizzes/assignments/coding), review-weighted avg rating,
active enrollments, completion rate, certificates, webinars hosted + registrations, and
`last_active`. The whole active roster is returned, zero-activity experts included (all-zero row).

**Attribution:** a course is credited to every user in `course.instructors` **and** its
`created_by` (of this institution). Co-taught courses count toward each instructor, so per-expert
sums can exceed the institution total — the payload states this in an `attribution` field so the
columns aren't misread as mutually exclusive. Content-authorship counts use `created_by` on the
content rows (exact per expert).

**Query strategy:** cost is a fixed ~12 grouped queries regardless of roster size. Per-course
aggregates (enrollments/completions/certificates) are computed once keyed by course id, credited
course-id sets are built per expert from the `instructors` M2M + `created_by`, and the two are
summed per expert in Python — no per-expert ORM loop. Content counts are one grouped query per
content type keyed by `created_by`. The detail endpoint raises `InstructorProfile.DoesNotExist`
→ 404 for a non-affiliate (never leaks another institution's expert). `EXPERT_CONTENT_ACTIVITY_ROLLUP`
is unbuilt; the content counts are computed inline here rather than depending on it.

Trend params: `?granularity=monthly|weekly` (default `monthly`), `?periods=N` (default 12, clamped `[1,24]`).
Top-courses params: `?sort=enrollments|rating|completion` (default `enrollments`), `?limit=N` (default 5, clamped `[1,50]`).

---

## Metrics

### Courses (`_course_metrics`)
One grouped query over `status` → `total`, `published`, `draft`, and a zero-filled `status_breakdown`
across all six `CourseStatus` values. One aggregate over published courses with `review_count > 0`
computes the **review-weighted** average rating: `SUM(avg_rating * review_count) / SUM(review_count)`
(both fields are denormalized on `NidusCourse`, maintained by the review service).

### Enrollments (`_enrollment_metrics`)
One conditional aggregate over active enrollments yields `active`, `completed` (→ `completion_rate`),
and `avg_progress` together. `all_time` drops the `is_active` filter. **Growth** counts `created_at`
in the current window `[now-N, now)` vs the previous `[now-2N, now-N)`; `growth_pct` is `None` when
there is no prior baseline (division by zero is not computable, so it is not faked as 0). **Active
learners** = distinct `user_id` with `last_accessed_at` inside the window (a learner in three courses
counts once).

### Certificates (`_certificate_metrics`)
`total` + `this_month` (`issued_at >= start of current month`), reached via
`enrollment__course__partner_institution`.

### Webinars (`_webinar_metrics`)
One grouped query over `status` → `total` + `draft`/`published`/`archived`. Time buckets
(`upcoming`/`live`/`completed`) are classified in Python over the small published-and-scheduled set
using `scheduled_at` + `duration_minutes` — cheap at institution scale and free of DB interval
arithmetic. One conditional aggregate over registrations gives active count + attended count.

### Roster (`_roster_metrics`)
`experts_active` (`affiliation_status='active'`) and `experts_total`.

### Engagement score (`_engagement_score`)
0–100 composite = weighted blend of completion rate, active-learner ratio, normalized avg rating, and
webinar attendance. Weights live in `_ENGAGEMENT_WEIGHTS` (sum 1.0) and the normalized components are
returned alongside the score so the frontend can show the breakdown.

---

## Trends (`build_time_series`)

`build_time_series(queryset, date_field, granularity, periods)` buckets rows with tz-aware
`TruncMonth` / `TruncWeek`, then **zero-fills** every period in Python — SQL only returns buckets that
have rows, so the series would otherwise be discontinuous. Returns
`[{period, count}, ...]` of length `periods`, oldest first. Month periods are keyed `YYYY-MM`, weeks
`YYYY-Www`.

**Bucket alignment (do not regress).** `_bucket_starts(now, granularity, periods)` computes the
**truncated** start of each bucket — day 1 of the month, or Monday of the week — and the query filter
uses `starts[0]` (the oldest bucket's true start), *not* an approximate `now - N*31 days` / `now - N
weeks` offset. Two bugs this prevents: (1) the oldest bucket undercounting rows that fall before the
offset but inside the bucket (worst at `periods=1`, where a day-offset would count only "today"); and
(2) the weekly series key drifting from the DB's `TruncWeek` (Monday) key across a year boundary —
e.g. a mid-week `now` keyed `2026-W00` while the Monday-truncated DB row keys `2025-W52`. Because both
the filter bounds and the series keys derive from the same truncated starts, filter and grouping match
exactly. Regression tests: `analytics/all_tests/test_analytics.py::AnalyticsTrendTests`
(`test_monthly_oldest_bucket_covers_full_month`, `test_weekly_oldest_bucket_covers_full_week`,
`test_bucket_starts_are_truncated`).

---

## Two honest gaps

The payload surfaces these rather than fabricating numbers:

- **Revenue** → `{"enabled": false, "estimated_gross": null}`. There is no payments/orders/transaction
  model in the codebase. `NidusCourse.price` / `Webinar.price` / `Enrollment.enrollment_type='paid'`
  exist but no money is recorded. Real revenue requires a payments app first.
- **Webinar attendance** → `attendance_rate` is computed but flagged `attendance_tracking_enabled:
  false`. `WebinarRegistration.attended` / `joined_at` are reserved for an unbuilt live-day join flow,
  so the rate reads 0 until that ships.

---

## Query strategy & performance

The summary is a fixed ~10 aggregate queries regardless of data volume — conditional aggregation
(`Count('id', filter=Q(...))`, `Avg(...)`) folds multiple counts into single queries, and there are no
per-row Python loops over large sets. The `NidusCourse(partner_institution, status)` composite index
(`idx_ncourse_inst_status`, migration `courses/0017`) backs the course counts. Enrollment/Certificate
reach the institution through joins on `course` that use existing indexes; `Webinar` already has
`idx_webinar_inst_status`.

Optional future caching (per-institution summary, TTL 5–15 min) is deferred — the dashboard is
read-heavy and staleness-tolerant, but load does not yet warrant it.

---

## Access-denied policy

Every endpoint derives the institution from the token and takes no resource id, so the only failure
mode is permission → **403** from `IsVerifiedPartnerInstitution`. This does not contradict the
project's numeric-ID → 404 rule: that rule applies to endpoints addressing a specific resource by id,
and these endpoints address none.
