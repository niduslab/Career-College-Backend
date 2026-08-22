# 29 — Instructor Dashboard & Analytics

A single merged dashboard page for individual instructors, combining what were previously two
separate mock pages (a KPI-tile dashboard and a richer "analytics" page) into one real,
honest surface. This is new backend work — the existing `analytics/` app only serves
`IsVerifiedPartnerInstitution`; an individual instructor account gets 403 from every endpoint in
it today.

Related: `20-analytics-dashboard.md` (the partner-institution analytics this mirrors the query
patterns of, but does not share endpoints with), `21-payments.md` (Order model), `15-review-rating-system.md`
(avg_rating/review_count), `27-learner-dashboard.md` (the honesty-rule precedent this follows).

---

## 1. Why merge, and why a new app surface instead of reusing `analytics/`

The two prior mock pages (`InstructorDashboardPage`, `AnalyticsPageContent`) duplicated the same
numbers — revenue, student count, course count, rating — under two different routes. One real
page removes that duplication.

`analytics/` cannot be reused as-is: every view in it is gated `IsVerifiedPartnerInstitution` and
every query is scoped by `request.user.partner_institution_profile`. An individual instructor has
no such profile. Rather than bolt an instructor branch onto partner-scoped views (mixing two very
different scopes — "my institution's courses" vs. "my own authored courses" — in one code path),
this is a **new service module** (`instructor_analytics_service.py`) inside the existing
`analytics` app, mirroring the partner service's query patterns (conditional aggregation, weighted
average, time-series helpers) without sharing its scope resolution.

## 2. Scope resolution: two paths to "my courses"

An instructor's courses come from **two** relations, and both must be checked — same dual-path
already used by `expert_performance_service.py`:

```python
Q(instructors=instructor) | Q(created_by=instructor)
```

`instructors` (M2M) covers co-instructors added to someone else's course; `created_by` covers
individual-instructor-authored courses. `.distinct()` is required — a course where the instructor
is both creator and roster member would otherwise double-count.

## 3. What is real, and what is deliberately dropped

Following the same honesty rule as `27-learner-dashboard.md` and `28-learning-paths.md`: every
number here is derived from a table that already exists. Nothing is invented.

| Original mock | Verdict | Why |
|---|---|---|
| Total Revenue | **Real** | `payments.Order`, `status='paid'`, scoped via the dual-path course filter. |
| Total Students | **Real** | `Enrollment`, distinct `user_id`, scoped via the dual-path course filter. |
| Course counts (draft/published/etc.) | **Real** | `NidusCourse.status`, conditional `Count(filter=Q(...))`. |
| Avg. Rating | **Real** | `NidusCourse.avg_rating` / `review_count`, weighted average across the instructor's courses. |
| Top Courses table | **Real** | Per-course enrollment/rating/revenue, same shape as the partner `top_courses`. |
| Completion funnel (enrolled → started → completed) | **Real** | `Enrollment.progress_percent` and `completed_at` are both persisted fields — no reimplementation of `recalculate_progress()` needed, just distinct-count aggregation per stage. |
| Watch-time trend (N-day chart) | **Dropped** | `WatchProgress.last_watched_at` is `auto_now` — it overwrites on every re-watch, so it is a "last touched" cursor, not an event log. A time-bucketed chart built on it would show recent-activity noise, not a real historical trend. Same caveat already documented for the learner dashboard's `total_learning_seconds`. If a real watch-time trend is wanted later, it needs a `LearnerActivityDay`-style append-only ledger first — do not build the chart on `last_watched_at`. |
| Traffic Donut (traffic source breakdown) | **Dropped** | No referrer/traffic-source tracking model exists anywhere in the codebase. Building this chart would mean inventing category percentages with no backing data. |
| AI Insights panel | **Dropped** | No AI/insights-generation service exists. Same rule as the learner dashboard's "Adjust with AI" — never fake an AI feature. |

## 4. Endpoint

One aggregate endpoint, mirroring the partner summary's shape (`InstitutionAnalyticsSummaryView`):

```
GET /api/v1/analytics/instructor/summary/
```

Gated `[IsAuthenticated, IsEmailVerified, IsInstructorUser]` — deliberately **not**
`IsVerifiedInstructor`: day-to-day dashboard viewing shouldn't require completed identity
verification, matching how `IsCourseCreator` (not `IsVerifiedCourseCreator`) gates ordinary
authoring elsewhere in this codebase.

Response shape:

```json
{
  "success": true,
  "data": {
    "revenue": {"gross": "24802.00", "currency": "BDT", "paid_orders": 42, "growth_pct": 12.5},
    "students": {"total": 1420, "active": 1310, "growth_pct": 4.2},
    "courses": {"total": 6, "published": 4, "draft": 2, "by_status": {"draft": 2, "published": 4, "...": 0}},
    "rating": {"avg_rating": 4.92, "review_count": 204},
    "funnel": {"enrolled": 1420, "started": 1180, "completed": 640},
    "top_courses": [
      {"id": 12, "title": "...", "slug": "...", "enrollments": 340, "avg_rating": 4.8, "revenue": "8200.00"}
    ]
  }
}
```

`revenue.growth_pct` / `students.growth_pct` follow the partner service's pattern: current vs.
previous 30-day window, `None` when there is no prior-window baseline to compare against (never a
fabricated 0%).

## 5. Query budget

Fixed cost, independent of course/enrollment volume — same design goal as the partner summary:

| Piece | Queries |
|---|---|
| Revenue (current + previous window) | 2 |
| Students (total, active, growth) | 2 |
| Course counts | 1 (conditional aggregation) |
| Rating (weighted avg) | 1 |
| Funnel (3 stages) | 3 (distinct-count each; could collapse to 1 conditional-count query — implementation may optimize this to match `conversion_funnel()`'s pattern) |
| Top courses | 1 (annotated, sliced) |

~10 queries total, matching the partner summary's budget.

## 6. Frontend

One page replaces both `InstructorDashboardPage` and `AnalyticsPageContent`. Sections, in order:
KPI tiles (revenue/students/courses/rating), completion funnel, top-courses table. No
watch-time chart, no traffic donut, no AI insights panel — removed along with their mock data,
not hidden behind a flag.

`RevenueChart` (the mock trend line) is **not** kept as-is either — it rendered a fabricated
week-over-week line with no real time-series backing it beyond the single growth_pct number this
endpoint provides. A real revenue trend chart is a separate future addition (mirroring the
partner `enrollment_trend`/`revenue_trend` pattern) if wanted; it is out of scope for this merge.
