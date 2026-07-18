# Postman Guide — Partner Institution Analytics Dashboard

Manual API testing for the institution analytics dashboard: KPI summary, trend series, and ranked
top-courses. All endpoints are **read-only** and scoped to the calling institution.

Architecture: `docs/architecture/20-analytics-dashboard.md`.

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `institution_token` | `Bearer eyJ...` | JWT for a **verified** partner institution (`is_verified=True, is_active=True`) |
| `other_institution_token` | `Bearer eyJ...` | JWT for a second, unrelated verified institution (isolation check) |
| `learner_token` | `Bearer eyJ...` | JWT for any learner — negative authz test |
| `expert_id` | `12` | `User.id` of an active affiliated expert (Group 4 detail) |

> **Precondition:** `institution_token` must be a **verified** institution (all endpoints are gated
> `IsVerifiedPartnerInstitution`). Seed some data first — create courses, get learners enrolled, publish
> a webinar or two, issue a certificate (drive an enrollment to 100%) — so the numbers are non-trivial.
> A fresh institution with no data returns a fully-formed payload of zeros (valid, but boring to test).

---

## Access-Denied Policy

These endpoints take **no resource id** — the institution is derived from the token. The only failure
mode is permission.

| Caller | Response |
|---|---|
| No token | **401** |
| Learner / instructor / unverified institution | **403** |
| Verified institution | **200** (its own data only) |

Cross-institution isolation is implicit: every query filters by the caller's own institution, so
`other_institution_token` sees only its own numbers — never Acme's.

---

## Group 1: Summary KPIs

### 1.1 Get the dashboard summary

```
GET {{base_url}}/analytics/partner/summary/
Authorization: {{institution_token}}
```

**200** — shape:

```json
{
  "success": true,
  "data": {
    "courses": {
      "total": 3, "published": 2, "draft": 1,
      "status_breakdown": {"draft": 1, "institution_review": 0, "under_review": 0,
                            "published": 2, "rejected": 0, "archived": 0},
      "avg_rating": 4.33, "total_reviews": 12
    },
    "enrollments": {
      "active": 3, "all_time": 3,
      "growth": {"current": 3, "previous": 0, "growth_pct": null, "window_days": 30},
      "active_learners": 2, "completion_rate": 33.3, "avg_progress": 46.7
    },
    "certificates": {"total": 1, "this_month": 1},
    "webinars": {
      "total": 3, "published": 2, "draft": 1, "archived": 0,
      "upcoming": 1, "live": 0, "completed": 1,
      "registrations": 2, "attendance_rate": 0.0, "attendance_tracking_enabled": false
    },
    "roster": {"experts_active": 1, "experts_total": 1},
    "revenue": {"enabled": false, "estimated_gross": null},
    "engagement_score": {"composite": 47.9,
      "components": {"completion": 33.3, "active_ratio": 66.7, "rating": 86.6, "attendance": 0.0}}
  }
}
```

**Checks:**
- `courses.total` = every course you own (any status); `published` + `draft` match your seed.
- `avg_rating` is **review-weighted** — a course with 10 reviews at 4.5 outweighs one with 2 at 3.5.
- `enrollments.active` excludes any enrollment on **another** institution's course.
- `growth.growth_pct` is `null` when the previous window had 0 enrollments (not computable, not faked).
- `revenue.enabled` is always `false` (no payments model). `attendance_tracking_enabled` always `false`.

### 1.2 Empty institution

Call 1.1 with a freshly-created verified institution that owns nothing.

**200** — every scalar `0`, every status bucket `0`, `growth_pct: null`, no missing keys.

### 1.3 Negative — learner forbidden

```
GET {{base_url}}/analytics/partner/summary/
Authorization: {{learner_token}}
```

**403.**

---

## Group 2: Trends

Three trend endpoints, identical param contract:
`enrollments/trend/`, `webinars/trend/`, `certificates/trend/`.

### 2.1 Monthly enrollment trend

```
GET {{base_url}}/analytics/partner/enrollments/trend/?granularity=monthly&periods=6
Authorization: {{institution_token}}
```

**200:**

```json
{"success": true, "data": {
  "granularity": "monthly", "periods": 6,
  "series": [
    {"period": "2026-02", "count": 0},
    {"period": "2026-03", "count": 0},
    {"period": "2026-04", "count": 1},
    {"period": "2026-05", "count": 0},
    {"period": "2026-06", "count": 2},
    {"period": "2026-07", "count": 0}
  ]
}}
```

**Checks:**
- `series` length == `periods`, **contiguous** (zero-filled months included), oldest first.
- Sum of `count` == total enrollments created in the window.

### 2.2 Weekly granularity

```
GET {{base_url}}/analytics/partner/certificates/trend/?granularity=weekly&periods=4
Authorization: {{institution_token}}
```

**200** — 4 weekly buckets keyed `YYYY-Www`.

### 2.3 Param clamping

```
GET {{base_url}}/analytics/partner/webinars/trend/?periods=999
Authorization: {{institution_token}}
```

**200** — `periods` clamped to **24**. An invalid/absent `granularity` defaults to `monthly`; a
non-numeric `periods` defaults to `12`.

### 2.4 Single period covers the whole bucket

```
GET {{base_url}}/analytics/partner/certificates/trend/?granularity=monthly&periods=1
Authorization: {{institution_token}}
```

**200** — one bucket. Its `count` must equal **every** certificate issued this calendar month,
including any issued on the 1st — not just today's. (Same for `granularity=weekly&periods=1`: the
single bucket counts from Monday of the current week, not from today.) This is the oldest-bucket
alignment guarantee; if an early-in-period record is missing, the bucket filter has regressed.

---

## Group 3: Top Courses

### 3.1 Ranked by enrollments (default)

```
GET {{base_url}}/analytics/partner/top-courses/?sort=enrollments&limit=5
Authorization: {{institution_token}}
```

**200:**

```json
{"success": true, "data": [
  {"id": 1, "title": "Published A", "slug": "published-a", "status": "published",
   "enrollments": 2, "completion_rate": 50.0, "avg_rating": 4.5, "review_count": 10},
  {"id": 2, "title": "Published B", "slug": "published-b", "status": "published",
   "enrollments": 1, "completion_rate": 0.0, "avg_rating": 3.5, "review_count": 2}
]
```

**Checks:**
- Only your institution's courses appear (no foreign courses).
- `sort=rating` orders by `avg_rating` then `review_count`; `sort=completion` by completed count.
- An invalid `sort` falls back to `enrollments`; `limit` clamped to `[1, 50]`.

### 3.2 Limit

```
GET {{base_url}}/analytics/partner/top-courses/?limit=1
Authorization: {{institution_token}}
```

**200** — exactly one course returned.

---

## Group 4: Expert Performance

Per-expert outcome metrics for the institution's active roster.

### 4.1 Roster performance list

```
GET {{base_url}}/analytics/partner/experts/performance/
Authorization: {{institution_token}}
```

**200:**

```json
{"success": true, "data": {
  "attribution": "a course is credited to every instructor and its creator",
  "experts": [
    {
      "expert": {"id": 12, "full_name": "Jane Roe", "email": "jane@x.com"},
      "department": "Data Science", "affiliation_status": "active",
      "affiliated_at": "2026-05-01T09:00:00Z",
      "courses_credited": 1, "published_courses": 1,
      "content": {"sections": 2, "lectures": 1, "quizzes": 0, "assignments": 0, "coding_exercises": 0},
      "avg_rating": 4.5, "total_reviews": 10,
      "enrollments": 2, "completion_rate": 50.0, "certificates": 1,
      "webinars_hosted": 1, "webinar_registrations": 1,
      "last_active": "2026-06-20T10:00:00Z"
    }
  ]
}}
```

**Checks:**
- **Every active affiliate** appears — a zero-activity expert is present with all counts `0` and `last_active: null` (the dashboard shows the whole roster).
- Metrics count only this institution's data — an enrollment on another institution's course is excluded even if the same expert somehow taught it.
- `attribution` is present: a co-taught course counts toward **each** instructor, so per-expert sums can exceed the institution totals — not a bug.
- `avg_rating` is review-weighted across the expert's credited **published** courses.

### 4.2 Single expert detail

```
GET {{base_url}}/analytics/partner/experts/{{expert_id}}/performance/
Authorization: {{institution_token}}
```

**200** — `data.expert` is one row of the shape above.

### 4.3 Foreign / unknown expert → 404

```
GET {{base_url}}/analytics/partner/experts/999999/performance/
Authorization: {{institution_token}}
```

**404** — a numeric id that isn't one of **your** active affiliates returns `Expert not found.` (never leaks another institution's expert).

---

## Cross-institution isolation

Run **1.1** with `other_institution_token`. The numbers must reflect **only** that institution's data.
No course, enrollment, certificate, webinar, or expert from Acme may appear in the other institution's
summary. This is the core security invariant of the feature.

---

# Admin (System-Wide) Analytics

Platform-wide counterpart under `analytics/admin/...`, gated `IsPlatformAdmin` — **no institution scope**.
Every query spans the whole platform (all institutions, all users).

Architecture: `docs/architecture/20-analytics-dashboard.md` (§ Admin surface).

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `admin_token` | `Bearer eyJ...` | JWT (or the `access_token` cookie from the shared admin login) for a user with `is_staff=True` or `user_type='admin'` |
| `learner_token` | `Bearer eyJ...` | Any non-admin token — negative authz test |

> **Precondition:** `admin_token` must be a platform admin. The shared login (`POST /api/v1/auth/login/`)
> returns JWT for an admin; you can also use the `access_token` cookie it sets. Seed data across a few
> institutions — courses, enrollments, PAID orders, certificates, webinars — so the platform totals are
> non-trivial.

---

## Access-Denied Policy

These endpoints take **no resource id** — access is derived from the token. The only failure mode is permission.

| Caller | Response |
|---|---|
| No token | **401** |
| Learner / instructor / institution (any non-admin) | **403** |
| Platform admin | **200** (platform-wide data) |

---

## Group 5: Platform Summary

### 5.1 Get the platform summary

```
GET {{base_url}}/analytics/admin/summary/
Authorization: {{admin_token}}
```

**200** — shape:

```json
{
  "success": true,
  "data": {
    "users": {
      "total": 120, "by_type": {"learner": 100, "instructor": 12, "partner_institution": 6, "admin": 2},
      "active": 118, "email_verified": 110,
      "new_this_window": 8, "growth_pct": 14.3
    },
    "courses": {
      "status_breakdown": {"draft": 5, "institution_review": 1, "under_review": 2,
                            "published": 20, "rejected": 1, "archived": 3},
      "published": 20, "avg_rating": 4.28
    },
    "enrollments": {
      "active": 300, "completed": 90, "completion_rate": 30.0,
      "by_type": {"free": 250, "paid": 50},
      "growth_pct": 12.5
    },
    "certificates": {"total": 90, "this_month": 15},
    "webinars": {"total": 8, "published": 5, "upcoming": 2, "live": 0, "completed": 3, "registrations": 40},
    "revenue": {
      "enabled": true, "currency": "BDT",
      "gross": 1500.0, "paid_orders": 2,
      "by_item_type": {"course": 1000.0, "webinar": 500.0},
      "this_window": 1500.0, "growth_pct": null
    }
  }
}
```

**Checks:**
- `users.total` = every account; `by_type` buckets sum to `total`.
- `courses.avg_rating` is **review-weighted** platform-wide (same rule as the partner summary).
- `enrollments.by_type` splits free vs paid; `completion_rate` = `completed / active` × 100.
- `revenue.enabled` is **`true`** (unlike partner, which is always `false`). `gross` = sum of `amount`
  over **PAID orders only** — `initiated`/`failed`/`cancelled` excluded.
- `revenue.growth_pct` is `null` when the previous window had 0 gross (not computable, not faked).

### 5.2 Negative — non-admin forbidden

```
GET {{base_url}}/analytics/admin/summary/
Authorization: {{learner_token}}
```

**403.** No token → **401**.

---

## Group 6: Trends

Four trend endpoints, same param contract as the partner trends
(`?granularity=monthly|weekly&periods=N`, `periods` clamped to `[1, 24]`):

| Endpoint | Series shape | Source |
|---|---|---|
| `admin/users/trend/` | `{period, count}` | `User.registration_date` |
| `admin/enrollments/trend/` | `{period, count}` | `Enrollment.created_at` |
| `admin/certificates/trend/` | `{period, count}` | `Certificate.issued_at` |
| `admin/revenue/trend/` | `{period, value}` | summed PAID-order gross per bucket |

### 6.1 Count trend (users / enrollments / certificates)

```
GET {{base_url}}/analytics/admin/users/trend/?granularity=monthly&periods=6
Authorization: {{admin_token}}
```

**200** — 6 contiguous monthly buckets, oldest first, rows `{period, count}` (zero-filled).

### 6.2 Revenue trend (summed, not counted)

```
GET {{base_url}}/analytics/admin/revenue/trend/?granularity=monthly&periods=6
Authorization: {{admin_token}}
```

**200** — rows are `{period, value}` where `value` is the **summed gross** of PAID orders in the bucket
(a float), **not** a row count.

```json
{"success": true, "data": {
  "granularity": "monthly", "periods": 6,
  "series": [
    {"period": "2026-02", "value": 0.0},
    {"period": "2026-03", "value": 500.0},
    {"period": "2026-04", "value": 0.0},
    {"period": "2026-05", "value": 1000.0},
    {"period": "2026-06", "value": 0.0},
    {"period": "2026-07", "value": 0.0}
  ]
}}
```

### 6.3 Param clamping

```
GET {{base_url}}/analytics/admin/revenue/trend/?periods=999
Authorization: {{admin_token}}
```

**200** — `periods` clamped to **24**; invalid/absent `granularity` defaults to `monthly`.

---

## Group 7: Top Courses & Funnel

### 7.1 Top courses (platform-wide)

```
GET {{base_url}}/analytics/admin/top-courses/?sort=enrollments&limit=10
Authorization: {{admin_token}}
```

**200** — same row shape as the partner top-courses, but **courses from every institution appear**
(no scope filter).

**Checks:**
- `sort ∈ {enrollments, rating, completion}`; invalid → falls back to `enrollments`.
- `limit` clamped to `[1, 50]`.

### 7.2 Conversion funnel

```
GET {{base_url}}/analytics/admin/funnel/
Authorization: {{admin_token}}
```

**200** — `stages`: distinct **learners** at `signup → enrolled → completed → certified`. Each stage
after `signup` carries `from_prev_pct` (the first stage omits the key — it has no previous stage).

```json
{"success": true, "data": {
  "stages": [
    {"key": "signup",    "label": "Signed up", "count": 100},
    {"key": "enrolled",  "label": "Enrolled",  "count": 60, "from_prev_pct": 60.0},
    {"key": "completed", "label": "Completed",  "count": 30, "from_prev_pct": 50.0},
    {"key": "certified", "label": "Certified",  "count": 28, "from_prev_pct": 93.3}
  ]
}}
```

**Checks:**
- Each stage is `{key, label, count}`; every stage except `signup` also has `from_prev_pct`.
- Counts are expected to be **monotonically non-increasing** down the funnel. **Known caveat:**
  `signup` counts current (non-deleted) learner accounts, while `enrolled`/`completed`/`certified`
  count distinct users on enrollment/certificate rows — a soft-deleted or role-changed learner can push
  a later stage above `signup` (`from_prev_pct > 100`). Tracked as a data-consistency bug.
