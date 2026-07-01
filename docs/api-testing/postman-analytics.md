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

## Cross-institution isolation

Run **1.1** with `other_institution_token`. The numbers must reflect **only** that institution's data.
No course, enrollment, certificate, webinar, or expert from Acme may appear in the other institution's
summary. This is the core security invariant of the feature.
