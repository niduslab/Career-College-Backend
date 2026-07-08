# 22 — Scheduled Courses (Cohort-Based Delivery)

This document explains the scheduled-courses feature end to end: what it is, the data model, the
schedule state machine, who may manage schedules, how learners enroll into a cohort, how content is
released week by week ("drip"), and what happens when the cohort ends. It is written to be readable
without prior knowledge of the feature — start at §1 and read down.

Related docs: `docs/future_implementations/SCHEDULED_COURSES.md` (the original design plan and phase
breakdown), `docs/api-testing/postman-schedules.md` (manual test walkthrough),
`12-enrollment.md` (the base enrollment system this builds on), `11-course-lifecycle.md` (the course
review state machine, which is untouched by this feature).

---

## 1. What problem does this solve?

Before this feature, every course on the platform was **self-paced and evergreen**: the instructor
authored the whole course up front, submitted it for review, and once published the content was
frozen and available to any enrolled learner forever.

Some courses don't work that way. A university-style course runs like a semester:

- Students sign up during a fixed **enrollment window** (e.g. two weeks before the term).
- The course **starts on a date**. Before that date, enrolled students can see the syllabus but not
  the content.
- Material is released **week by week** — the instructor uploads the week-2 module during week 1 or
  week 2, while the course is already live and students are already enrolled.
- There may be a **seat cap** ("this cohort takes 50 students").
- When the term ends, students keep access to everything (lifetime access), but new students can't
  join that run — they wait for the next cohort.

Scheduled courses add exactly this, as a **layer on top of the existing course**, without changing
how self-paced courses behave.

## 2. The core idea: a course plus a schedule

We deliberately did **not** add start/end dates to the course itself. Instead there is a separate
model, **`CourseSchedule`**, that wraps a course:

```
NidusCourse (the curriculum template — sections, lectures, quizzes, …)
    └── CourseSchedule "Fall 2026 Batch"   (dates + seat cap + status)
    └── CourseSchedule "Spring 2027 Batch" (a later re-run of the same course)
```

- The **course** stays the single source of the curriculum. It still goes through the normal
  authoring and admin-review flow described in `11-course-lifecycle.md`.
- The **schedule** holds everything time-related: when enrollment opens/closes, when the cohort
  starts and ends, and how many seats it has.
- One course can have many schedules over time — re-running a course next term means creating a new
  schedule, not duplicating the course.
- A course with **no** schedules is simply a self-paced course. Nothing about it changes.

## 3. Data model

### 3.1 `CourseSchedule` (`courses/all_models/schedule_models.py`)

| Field | Meaning |
|---|---|
| `course` | FK → `NidusCourse`, `related_name='schedules'`. Cascade-deleted with the course. |
| `cohort_label` | Optional human name, e.g. "Fall 2026 Batch". |
| `timezone` | IANA name (default `UTC`) the dates are *presented* in. Stored but not validated — mirrors `Webinar.timezone`. All comparisons happen on the tz-aware datetimes themselves. |
| `enrollment_opens_at` / `enrollment_closes_at` | The window during which learners may join. |
| `start_date` | When the cohort goes live. Before this, enrolled learners see the outline but no content. |
| `end_date` | Nullable. When the cohort is considered finished (bookkeeping only — see §8). Null = open-ended. |
| `max_seats` | Nullable seat cap. Null = unlimited. |
| `status` | `draft → scheduled → ongoing → completed → archived` (see §4). |
| `created_by` / `last_edited_by` | From `AuthoredModel` — who created/last touched the schedule. |

Inherits `AuthoredModel`, so schedule rows carry authorship like every other expert-authored object.

### 3.2 `CourseSection.unlocks_at` (drip lock)

Each section may carry an optional **`unlocks_at`** datetime. This is the drip-release mechanism:

- `unlocks_at = NULL` → the section is available immediately (the default; all pre-existing
  sections behave exactly as before).
- `unlocks_at` in the future → the section is **locked** for learners until that moment.

The lock is **section-level**, matching the "week 1 module / week 2 module" mental model. It is set
through the normal section create/edit endpoints (`CourseSectionCreateUpdateSerializer` accepts
`unlocks_at`).

### 3.3 `Enrollment.schedule`

`Enrollment` gained a nullable FK `schedule` → `CourseSchedule`:

- `schedule = NULL` → a classic **self-paced** enrollment. All old behavior preserved.
- `schedule` set → a **cohort** enrollment. The learner's access follows the cohort's timeline.

The old single unique constraint `(user, course)` was replaced by **two partial uniques**:

| Constraint | Rule it enforces |
|---|---|
| `(user, course) WHERE schedule IS NULL` | A learner still has at most one self-paced enrollment per course, ever. |
| `(user, schedule) WHERE schedule IS NOT NULL` | One enrollment per learner per cohort — but the same learner may join a *different* cohort of the same course later (retake next term). |

This is the same partial-unique pattern already used by `payments.Order` and messaging's
conversation constraints.

Migration `courses/0018` created all of the above. Every change is additive and nullable — existing
rows needed no backfill.

## 4. The schedule state machine

```
draft ──activate──▶ scheduled ──(start_date passes)──▶ ongoing ──(end_date passes)──▶ completed ──archive──▶ archived
  ▲                    │                                                                                        │
  └────── rework ──────┘◀───────────────────────────── rework ─────────────────────────────────────────────────┘
```

`CourseSchedule.transition_to(new_status, actor=None)` is the **single entry point** — never set
`status` directly (same rule as `NidusCourse` and `Webinar`).

| Transition | Who / what triggers it |
|---|---|
| `draft → scheduled` | Owner calls `POST .../activate/`. Runs `_validate_activation()` (below). |
| `scheduled → draft` | Owner calls `POST .../rework/` — the safety valve for a premature activation. |
| `scheduled → ongoing` | **Automatic.** Celery-beat task flips it when `start_date` passes. |
| `ongoing → completed` | **Automatic.** Beat task flips it when `end_date` passes (null `end_date` stays ongoing). |
| `completed → archived` | Owner calls `POST .../archive/`. |
| `archived → draft` | Owner calls `POST .../rework/` (reuse the row for another run). |

**Activation validation** (`_validate_activation`) — activating a schedule requires, all at once:

1. The course itself is `published` (a schedule can never make unreviewed content reachable).
2. `enrollment_opens_at < enrollment_closes_at <= start_date` (window closes before or at start).
3. `end_date > start_date` when an end date is set.
4. `enrollment_closes_at` and `start_date` are in the future.

Violations come back as a field-keyed `ValidationError` dict → HTTP 400 with an `errors` object.
Illegal transitions raise a plain-string `ValidationError` → HTTP 422.

**Editability:** `is_editable()` is `status in {draft, scheduled}`. Dates, label, and seat cap can be
PATCHed until the cohort actually starts; once `ongoing`, the schedule row is frozen (422). Deletion
is allowed only in `draft`.

**The beat task** — `advance_course_schedules_task` (`courses/tasks.py`, every 5 min via
`CELERY_BEAT_SCHEDULE`) advances each due row with a per-row `transition_to()` inside its own
try/except, so validation still applies and one bad row can't block the sweep. Statuses therefore
always follow the dates without anyone remembering to click a button.

## 5. Who manages schedules (ownership)

Schedule endpoints live under the course:

```
GET  /api/v1/courses/<pk>/schedules/                    list (paginated)
POST /api/v1/courses/<pk>/schedules/                    create
GET  /api/v1/courses/<pk>/schedules/<id>/               detail
PATCH/DELETE /api/v1/courses/<pk>/schedules/<id>/       edit (draft|scheduled) / delete (draft only)
POST /api/v1/courses/<pk>/schedules/<id>/activate|archive|rework/
```

All are gated `IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator`; the *object-level*
ownership rule lives in `courses/services/schedule_service.py`:

| Course type | Who may **mutate** schedules | Who may **read** them |
|---|---|---|
| Institution-owned (`partner_institution` set) | Only the owning institution | Institution + the course's roster experts |
| Individual-instructor | Only `created_by` | Same |

This mirrors the platform's existing split: institutions own operational decisions (roster, dates),
experts author content. A roster expert can *see* the timeline they're teaching against but cannot
move dates or change capacity.

**Access-denied policy:** every schedule URL uses numeric IDs, so no-access is always **404** with
the same body a truly-missing course produces (`"Course not found."`) — existence is never leaked.
Business-rule refusals (delete a non-draft, patch an ongoing) are **422**. `ScheduleError(message,
http_status)` carries the status, mirroring `WebinarError`.

## 6. Enrolling into a cohort

The existing enroll endpoint gained one optional field:

```
POST /api/v1/courses/<slug>/enroll/
{ "schedule_id": 12 }        ← omit for classic self-paced enrollment
```

Inside `enroll_learner(user, course, *, enrollment_type, allow_unpublished, schedule=None)`
(`courses/services/enrollment_service.py`), a cohort enrollment passes three extra checks:

1. **Status** — the schedule must be `scheduled` (a draft/archived/completed cohort never accepts
   members; `ongoing` can't either, because the window always closes at or before start).
2. **Window** — `enrollment_opens_at <= now <= enrollment_closes_at`, else 422
   `"Enrollment for this cohort is not open."`
3. **Capacity** — if `max_seats` is set, the service takes `select_for_update()` **on the schedule
   row first**, then counts active enrollments. The row lock means two learners racing for the last
   seat serialize — the same over-subscription fix webinar registration uses. Full → 422
   `"This cohort is full."`

Paid courses work unchanged: the price gate in `CourseEnrollView` (PAID `Order` required when
`price > 0`) runs before the schedule logic, so a learner buys the course once and can then join a
cohort. `Order` knows nothing about schedules — payment buys the course, `schedule_id` picks the
cohort at enroll time.

A learner may hold a self-paced enrollment *and* a cohort enrollment for the same course (the
partial uniques allow it). Access checks then use the most permissive row — self-paced first — since
a learner who already had ungated lifetime access can't lose it by also joining a cohort.

## 7. What the learner sees (release gates)

Two independent, learner-only gates decide whether a piece of content is reachable. Both live in one
service function, `assert_content_released(enrollment, section)`
(`courses/services/learner_service.py`), so every endpoint enforces identical rules:

1. **Cohort window** — a schedule-bound enrollment gets **no content at all** before the cohort's
   `start_date` → 422 `"This course has not started yet."`
2. **Section drip lock** — a section with a future `unlocks_at` is locked **for every learner**
   (cohort or self-paced) → 422 `"This content has not been released yet."`

Where the gates run:

- **Curriculum** (`GET /learn/<slug>/curriculum/`) — never blocks. Locked sections are still listed
  (learners should see what's coming) but carry `"is_locked": true` and their `unlocks_at`, so the
  frontend can grey them out with an unlock date. Before the cohort starts, *every* section is
  marked locked.
- **Detail endpoints** (`/learn/lectures/<id>/`, `/learn/quizzes/<id>/`,
  `/learn/assignments/<id>/`, `/learn/coding-exercises/<id>/` + its run/submit) — the consumption
  loaders call the gate right after the access check, so a locked item returns **422**.
- **Write endpoints** (`/progress/`, quiz `/submit/`, assignment `/submit/`) — these fetch the
  enrollment inline rather than through a loader, so they call the same gate explicitly before
  accepting the write. A learner can't record progress against, or submit into, content they can't
  see.

Why **422** and not 403/404: the learner has legitimate access (they're enrolled); the refusal is a
*timing* rule, the same family as "course is already published" business-rule responses. 404 would
lie about existence; 403 would suggest a permissions problem the learner could never fix.

**Instructor bypass:** `assert_content_released` is a no-op when there is no enrollment — i.e. for
the course's own instructors/experts previewing content. Authors must be able to QA week-3 material
before its unlock date; the curriculum also reports `is_locked: false` throughout for them.

## 8. Uploading content while the cohort runs (drip authoring)

The historical rule is that a `published` course is frozen (`NidusCourse.is_editable()` allows only
`draft`/`rejected`). Scheduled courses need the opposite: the instructor uploads week 2 while the
course is live.

The carve-out is deliberately narrow, in **one** function — `guard_editable(course)`
(`courses/utils.py`), the guard every content-editing view already calls:

> A `published` course **with at least one `ongoing` schedule** is content-editable. Everything else
> keeps the old rule.

Consequences worth knowing:

- Self-paced courses are untouched — no ongoing schedule ever exists for them.
- The window opens exactly when a cohort is running and closes again when it completes: before
  `start_date` (status `scheduled`) the course is still frozen, and after `end_date` flips the
  schedule to `completed` the freeze returns automatically.
- The admin-review pipeline is **not** re-entered for weekly additions. The initial review gate is
  unchanged (a course must pass review to be published at all, with at least week-1 content), but
  drip additions ride on the trust already placed in verified instructors — the same trust that let
  them edit freely pre-publish. Authorship of every added row is stamped via `AuthoredModel`
  (`created_by` / `last_edited_by`), so institutions can audit who added what, when.

## 9. When the cohort ends

Per the locked design decision: **learners keep full lifetime access.** `end_date` revokes nothing.

What actually happens at `end_date`:

- The beat task flips the schedule to `completed` (bookkeeping / analytics only).
- The `guard_editable` carve-out closes — content is frozen again.
- New enrollments were already impossible (the window closed at or before `start_date`).
- Progress, quiz/assignment/coding submission, and certificate issuance all keep working —
  submission cut-off at end date was considered and deliberately **not** implemented (see the plan
  doc §10 for the open follow-up).

## 10. File map

| Concern | File |
|---|---|
| Model + state machine | `courses/all_models/schedule_models.py` |
| Enrollment FK + partial uniques | `courses/all_models/enrollment_models.py` |
| Section `unlocks_at` | `courses/all_models/course_models.py` (`CourseSection`) |
| Ownership + CRUD service, `ScheduleError` | `courses/services/schedule_service.py` |
| Cohort enroll checks (window/capacity) | `courses/services/enrollment_service.py` (`_assert_schedule_enrollable`, `enroll_learner`) |
| Release gates + curriculum lock markers | `courses/services/learner_service.py` (`ContentNotReleasedError`, `assert_content_released`, `load_learner_curriculum`) |
| Drip-authoring carve-out | `courses/utils.py` (`guard_editable`) |
| Schedule views | `courses/all_views/schedule_views.py` |
| Enroll view (`schedule_id`) | `courses/all_views/enrollment_views.py` |
| Learner 422 handling | `courses/all_views/learner_views.py` |
| Serializers | `courses/all_serializers/schedule_serializers.py`, section serializers in `content_serializers.py` |
| Beat task | `courses/tasks.py` (`advance_course_schedules_task`) + `CELERY_BEAT_SCHEDULE` in `settings.py` |
| Migration | `courses/migrations/0018_courseschedule_and_more.py` |
| Tests | `courses/all_tests/test_course_schedules.py` |

## 11. Design decisions, briefly

- **Wrapper model, not fields on the course** — a course re-runs; dates don't belong on the
  template. Multiple cohorts per course fall out for free.
- **Section-level drip, not per-lecture** — matches how instructors think ("week 2 module") and
  keeps both authoring and gating simple.
- **Automatic date-driven transitions** — statuses are pure functions of the dates, so a beat task
  owns them; humans only make the genuinely human calls (activate, archive, rework).
- **One gate function** — every learner endpoint calls `assert_content_released`; there is exactly
  one place the release rules can be right or wrong.
- **422 for timing refusals** — enrolled learners get an honest, actionable "not yet" instead of a
  misleading 403/404.
- **No re-review for drip content** — weekly re-approval would stall every cohort weekly; passive
  auditability via `AuthoredModel` stamping was chosen instead.
- **Lifetime access after end** — parity with self-paced; an end date stops the *run*, not the
  learning.
