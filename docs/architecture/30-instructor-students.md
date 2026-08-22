# 30 — Instructor Students Roster

## Problem

An instructor has no way to see *who* is enrolled in their courses. The
analytics summary (`29-instructor-dashboard-analytics.md`) counts students but
never lists them — `_student_metrics` returns `total` / `active` / `growth_pct`
and nothing else.

The frontend page at `/dashboard/instructor/students` exists but is entirely
seeded with hardcoded mock rows (Amelia Watson, James Carter, …). This feature
replaces that mock with a real, paginated, searchable roster.

## Scope: one instructor's own students

"My students" = every learner holding an `Enrollment` on a course the
instructor owns. Ownership is the same dual-path rule used everywhere else:

```python
Q(course__instructors=instructor) | Q(course__created_by=instructor)
```

`.distinct()` is mandatory — a course where the instructor is *both* a roster
member and `created_by` would otherwise double every row.

**One row = one enrollment, not one learner.** A learner enrolled in three of
the instructor's courses appears three times, once per course, each with its
own progress and last-accessed timestamp. This is deliberate: the instructor's
question is "how is this person doing in *this* course", which a
learner-deduplicated list cannot answer. The summary block reports distinct
learner counts separately so the two never contradict each other.

## What is real, and what we refuse to invent

Every column below maps to a column that already exists. Nothing is computed
from a formula that has no backing table.

| Field | Source | Notes |
|---|---|---|
| `student.full_name` | `User.full_name` | |
| `student.email` | `User.email` | |
| `student.avatar` | `LearnerProfile.profile_photo` | Media-root-**relative** path (`/media/…`), not absolute — the frontend's `mediaUrl()` prepends the API origin it already knows. Building it with `build_absolute_uri` would bake in whatever `Host` header the request arrived with. Null when never uploaded; the frontend falls back to initials. |
| `course.title` / `slug` / `id` | `Enrollment.course` | |
| `progress_percent` | `Enrollment.progress_percent` | Denormalized, maintained by `recalculate_progress()`. |
| `enrolled_at` | `Enrollment.created_at` | |
| `last_active_at` | `Enrollment.last_accessed_at` | Course-scoped. Nullable — a learner who enrolled and never opened content has `null`, which the frontend must render as "Never", not as a date. 5-minute write debounce (`LAST_ACCESSED_DEBOUNCE`). |
| `completed_at` | `Enrollment.completed_at` | Sticky — never cleared, see the completion rule in `CLAUDE.md`. |
| `is_active` | `Enrollment.is_active` | False = soft-unenrolled. |
| `enrollment_type` | `Enrollment.enrollment_type` | free / paid / admin_granted. |
| `has_certificate` | `Exists(Certificate on enrollment)` | |
| `status` | **derived**, see below | |
| `cohort` | `Enrollment.schedule.cohort_label` | Null for self-paced. |

### Derived status — the one computed field, and its exact rule

`status` is not stored. It is computed in Python from three real columns so the
rule lives in one documented place instead of being re-invented per client:

```
completed_at is not null            -> "completed"
is_active is False                  -> "unenrolled"
last_active_at is null              -> "not_started"
last_active_at older than 14 days   -> "inactive"
otherwise                           -> "active"
```

`INACTIVE_AFTER_DAYS = 14` is a **product threshold, not a measurement**. It is
named and exported so it is obviously a policy knob, and the response echoes it
back as `inactive_after_days` so the frontend never hardcodes its own copy.
Order matters: a completed learner who stopped opening the course is
`completed`, not `inactive`.

Note this is deliberately **not** the same as the analytics funnel's "started"
(`progress_percent > 0`). A learner can open a lecture (setting
`last_accessed_at`) without completing anything (`progress_percent` still 0).
The roster cares about attendance; the funnel cares about achievement.

### Deliberately absent

- **"Engagement tips" with statistics.** The mock page claimed things like
  "students who finish Module 1 are 3× more likely to complete". No cohort
  analysis exists to support that. Not returned; do not add it without a real
  study behind it.
- **Platform-wide "last seen".** `LearnerActivityDay` records day-granular
  activity across the whole platform, not per course. Mixing it into a
  per-course roster would show a learner as "active today" because they studied
  someone *else's* course. `Enrollment.last_accessed_at` is the honest column.
- **Watch time per student.** Same reason as the dashboard: `WatchProgress`
  stores a furthest-cursor position, not accumulated playback, and
  `last_watched_at` is `auto_now` so no history survives.

## Endpoints

Both gated `[IsAuthenticated, IsEmailVerified, IsInstructorUser]` — matching the
analytics summary. Day-to-day roster viewing does not require completed identity
verification, consistent with `IsCourseCreator` gating ordinary authoring.

### `GET /api/v1/analytics/instructor/students/`

Paginated roster. Standard envelope + `StandardResultsSetPagination`
(`page_size` default 10, max 100).

Query params — all optional, all validated, unknown values are a 400 not a
silent ignore:

| Param | Values | Default |
|---|---|---|
| `search` | ≥2 chars; matches `full_name` or `email` (icontains, trigram-indexed) | — |
| `course_id` | int; must be a course the caller owns, else 400 | all owned courses |
| `status` | `active` / `inactive` / `completed` / `not_started` / `unenrolled` | all |
| `sort` | `-last_active` / `last_active` / `-enrolled` / `enrolled` / `-progress` / `progress` / `name` | `-last_active` |
| `page`, `page_size` | paginator | 1, 10 |

`status` filtering happens **in SQL**, not by filtering the serialized page —
otherwise pagination counts would be wrong (page 1 could return 3 rows of 10).
`_status_filter_q()` translates each status to the same predicate the derived
field uses, so the two can never drift.

Every sort appends an `'id'` tiebreaker. Without it, rows with equal
`last_accessed_at` (very common — `null` for everyone who never opened the
course) can be skipped or duplicated across pages.

### `GET /api/v1/analytics/instructor/students/summary/`

The KPI cards above the table, plus the sidebar aggregates. Separate from the
list because it must describe the **whole** roster, not the current page — the
learner-dashboard `status_counts` precedent (`CLAUDE.md`, My Courses) exists
because a frontend counting rows in one page caps out at `page_size` and
silently lies.

Returns:

```json
{
  "success": true,
  "data": {
    "total_students": 128,
    "active_students": 74,
    "avg_progress": 41.3,
    "new_this_period": 12,
    "new_growth_pct": 33.3,
    "window_days": 30,
    "inactive_after_days": 14,
    "status_breakdown": {
      "active": 74, "inactive": 22, "completed": 18,
      "not_started": 9, "unenrolled": 5
    },
    "top_courses": [
      {"id": 4, "title": "Web Development", "slug": "web-development", "students": 40}
    ]
  }
}
```

- `total_students` / `active_students` are **distinct learners** (a learner in
  three courses counts once), while `status_breakdown` counts **enrollment
  rows** — the two intentionally do not sum to the same number, and the field
  names say which is which.
- `avg_progress` averages `progress_percent` over active enrollments only.
  Including soft-unenrolled rows would drag it down with abandoned records.
- `new_growth_pct` is `null` when the previous window had zero enrollments —
  the same no-baseline rule as the dashboard. Never render a fake `0%`.

## Query budget

List: 3 queries (count, page, prefetch-free thanks to `select_related`) plus
the `Exists` subquery inlined into the page query. Independent of page depth.

Summary: 6 aggregates, independent of roster size.

`select_related('user', 'user__learner_profile', 'course', 'schedule')` covers
every serialized field; `has_certificate` rides an `Exists` annotation rather
than a `select_related('certificate')` join so a missing certificate does not
need exception handling per row.

## File layout

| File | Role |
|---|---|
| `analytics/services/instructor_students_service.py` | Query building, status derivation, row shape, aggregates |
| `analytics/all_views/instructor_students_views.py` | Two `APIView`s |
| `analytics/urls.py` | Two routes under `instructor/` |

The `analytics` app has **no serializer layer** — it owns no models and every
endpoint returns plain dicts built in its service. `serialize_student_row()`
follows that existing convention rather than introducing an `all_serializers/`
directory for one feature.

The service lives in `analytics/` rather than `courses/` because it is a
read-only aggregation over other apps' models and owns no writes — the same
reasoning that put the analytics app there in the first place. It does not
belong in `instructor_analytics_service.py`: that module answers "how is my
business doing" in fixed-size aggregates, this one answers "who are my people"
and returns paginated rows.

## Frontend

`src/lib/instructor-students-api.ts`, `src/hooks/use-instructor-students.ts`,
and a rewrite of `src/components/dashboard/instructor/students-page.tsx`
replacing `SEED_STUDENTS` / `STATS` / `TOP_COURSES` / `TIPS` with the two real
endpoints. Filters move from client-side `Array.filter` to query params so they
apply across the whole roster instead of the current page.
