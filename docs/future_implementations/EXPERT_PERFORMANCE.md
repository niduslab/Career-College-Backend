# Expert Performance Metrics (Institution)

**Status:** ✅ Implemented.
**Shipped:** `analytics/services/expert_performance_service.py` (`expert_performance(institution, *, expert_id=None)`), views `InstitutionExpertPerformanceView` / `InstitutionExpertPerformanceDetailView` in `analytics/all_views/analytics_views.py`, routes `partner/experts/performance/` + `partner/experts/<int:expert_id>/performance/`, tests `analytics/all_tests/test_expert_performance.py`. No migration. Docs: CLAUDE.md + `docs/architecture/20-analytics-dashboard.md` + `docs/api-testing/postman-analytics.md` (Group 4). **Note:** `EXPERT_CONTENT_ACTIVITY_ROLLUP.md` is still unbuilt, so the content-authorship counts are computed **inline** here (grouped `created_by` queries per content type) rather than reusing a shared rollup service — extract the shared helper if/when the rollup ships.
**Depends on:** the `analytics` app, `AuthoredModel` authorship (migration `courses/0016`), the expert roster (`InstructorProfile.affiliated_institution`) — all shipped.
**SRS:** §7.2.1 "track expert-created content performance", §7.7 reporting.

---

## 1. Overview

The analytics dashboard reports **institution-wide** KPIs. This feature drills one level down: **per-expert outcome metrics** — not just "how much did each expert produce" (that's the content-activity rollup) but "how well is each expert's teaching performing" — ratings, enrollments, completions, certificates, and webinar hosting, scoped to the institution's own roster.

Lives in the existing `analytics` app (course/webinar-shaped data, same audience). New endpoints under `partner/experts/`:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/analytics/partner/experts/performance/` | Ranked/listed per-expert performance rows for the whole active roster. |
| `GET /api/v1/analytics/partner/experts/<expert_id>/performance/` | One expert's detail (numeric id → 404 if not this institution's affiliate). |

**Auth (all):** `IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution`. Every query scoped to `request.user.partner_institution_profile`.

---

## 2. Attribution rule (decide once, document in code)

A course can have multiple instructors (`NidusCourse.instructors` M2M) plus a `created_by`. **An expert is credited for a course when they are in `course.instructors` OR are its `created_by`, AND the course's `partner_institution` is this institution.** A co-taught course counts toward *each* instructor — so per-expert enrollment sums can exceed the institution total. Document this in the response (`"attribution": "course credited to every instructor"`) so nobody reads the columns as mutually exclusive.

Content-authorship metrics (sections/lectures/… authored) use `created_by` on the content rows (the activity-rollup path), which is exact per expert.

---

## 3. Metrics (per expert)

Each: **what** · **why** · **fields** · **calc**.

**1. Courses credited**
- Distinct institution courses where the expert is an instructor or `created_by`.
- Teaching load / spread.
- `NidusCourse.instructors`, `NidusCourse.created_by`, `partner_institution`.
- `NidusCourse.objects.filter(partner_institution=inst).filter(Q(instructors=expert) | Q(created_by=expert)).distinct().count()`. Also `published_courses` (same + `is_published=True`).

**2. Content authored** (reuse activity rollup)
- Counts of sections / lectures / quizzes / assignments / coding exercises the expert `created_by`.
- Authoring output.
- `created_by` on each content model (institution reached via `section__course__partner_institution`).
- One grouped query per content type keyed by `created_by` — identical to `EXPERT_CONTENT_ACTIVITY_ROLLUP.md`. **Share that code** — do not re-implement.

**3. Average rating**
- Review-weighted mean rating across the expert's credited published courses.
- Teaching quality signal.
- `NidusCourse.avg_rating`, `review_count` (denormalized).
- `SUM(avg_rating*review_count)/SUM(review_count)` over the expert's credited published courses with `review_count>0`; guard 0.

**4. Total enrollments**
- Active enrollments across the expert's credited courses.
- Reach.
- `Enrollment.course__partner_institution`, `Enrollment.course__instructors`/`created_by`, `is_active`.
- Count active enrollments on credited courses.

**5. Completion rate**
- % of those enrollments completed.
- Outcome / efficacy of the expert's courses.
- `Enrollment.completed_at`.
- `completed / total` on credited courses; conditional aggregation; guard 0.

**6. Certificates issued**
- Certificates on the expert's credited courses.
- Verified completions attributable to the expert.
- `Certificate.enrollment__course__…`.
- Count.

**7. Webinars hosted + registrations**
- Webinars where the expert is `host_expert`; sum of active registrations on them.
- Live-teaching contribution (webinar editing is institution-only, but the expert hosts).
- `Webinar.host_expert`, `WebinarRegistration.is_active`.
- Count webinars + count registrations on them. (`institutional_speakers` credit optional — decide at build; recommend hosted-only for v1.)

**8. Last active**
- Most recent `created_at` across the expert's authored content (+ optionally last course publish).
- Staleness / engagement.
- `Max(created_at)` from the authored-content queries.

**9. Affiliation context** (passthrough, not computed)
- `department` (name), `affiliation_status`, `affiliated_at`, `is_verified`.
- Lets the dashboard group by department and flag removed experts.
- `InstructorProfile` fields.

---

## 4. Response shape

```json
{
  "success": true,
  "data": {
    "attribution": "a course is credited to every instructor and its creator",
    "experts": [
      {
        "expert": {"id": 12, "full_name": "Jane Roe", "email": "jane@x.com"},
        "department": "Data Science",
        "affiliation_status": "active",
        "courses_credited": 4, "published_courses": 3,
        "content": {"sections": 6, "lectures": 24, "quizzes": 8, "assignments": 5, "coding_exercises": 3},
        "avg_rating": 4.4, "total_reviews": 120,
        "enrollments": 812, "completion_rate": 58.3, "certificates": 240,
        "webinars_hosted": 2, "webinar_registrations": 430,
        "last_active": "2026-06-20T10:00:00Z"
      }
    ]
  }
}
```

- **Every active affiliate listed**, including zero-activity experts (all counts 0) — the dashboard shows the whole roster.
- Detail endpoint returns a single `expert` object of the same shape (optionally with a `courses: [...]` per-course breakdown).

---

## 5. Query strategy

The roster is small (tens, not thousands), so cost is bounded regardless of approach. Prefer **grouped queries keyed by expert id**, merged in Python, over a per-expert loop:

- Content counts: 6 grouped queries (`.values('created_by').annotate(...)`) — reuse the rollup service.
- Courses / enrollments / certificates: group over `NidusCourse`/`Enrollment`/`Certificate` filtered to the institution, annotated/`.values()` by the instructor id via the M2M through relation. Co-taught rows naturally fan out to each instructor — matches the attribution rule.
- Webinars: group `Webinar` by `host_expert`; registrations by `webinar__host_expert`.
- One roster query to zero-fill.

Total ≈ a dozen grouped queries, independent of roster size. **No per-row ORM calls.** If a per-expert loop is used instead for clarity, cap it and `log()` nothing (info logging is disallowed — errors only).

Add index if needed: enrollment/certificate reach the institution via `course` joins (existing indexes cover). The M2M `NidusCourse.instructors` through table is indexed by Django by default.

---

## 6. Files to change

1. **`analytics/services/analytics_service.py`** (or a new `analytics/services/expert_performance_service.py` if it grows) — `expert_performance(institution, *, expert_id=None) -> list[dict] | dict`. Reuse the content-count aggregation from the activity rollup (import or share a helper). Raise a scoped `DoesNotExist`/`AnalyticsError(404)` for an `expert_id` not affiliated with the institution.
2. **`analytics/all_views/analytics_views.py`** — `InstitutionExpertPerformanceListView`, `InstitutionExpertPerformanceDetailView`. Same permission stack; standard envelope; `try/except` → log + 500; numeric-id detail → 404.
3. **`analytics/urls.py`** — `partner/experts/performance/` and `partner/experts/<int:expert_id>/performance/`.
4. **`analytics/all_tests/test_expert_performance.py`** — new suite.
5. **`CLAUDE.md`** + **`docs/architecture/20-analytics-dashboard.md`** — extend the analytics section.
6. **`docs/api-testing/postman-analytics.md`** — add a group.

**No migration** (read-only over existing columns) unless profiling shows a missing index.

---

## 7. Edge cases & rules

- **Attribution overlap:** co-taught course counts for each instructor — per-expert sums exceed institution totals by design. Surfaced in the payload.
- **Removed experts:** default to `affiliation_status='active'`; historical content stays attributed (`created_by` is `SET_NULL` but the user isn't deleted). Optional `?include_removed=true`.
- **Null-author legacy rows** (pre-migration 0016) drop out of content counts silently — same caveat as the rollup.
- **403 vs 404:** list endpoint (no id) → 403 only. Detail (`<int:expert_id>`) → 404 for a foreign/unknown expert (never leak).
- **Cross-institution safety:** every query filtered by the token's institution.
- **Divide-by-zero:** rating / completion guard empty denominators → `0`.

---

## 8. Build order

1. List endpoint, content + course + enrollment + completion metrics (reuse rollup). Roster zero-filled.
2. Add ratings + certificates.
3. Add webinar hosting metrics.
4. Detail endpoint (+ optional per-course breakdown).
5. `?department=<id>` filter and `?sort=` (rating / enrollments / completion).

## 9. Future extensions

- Trend per expert (enrollments/ratings over time) via `build_time_series`.
- Department rollup (average of the department's experts).
- Merge the content-activity rollup and this endpoint into one expert-analytics surface if they converge.
