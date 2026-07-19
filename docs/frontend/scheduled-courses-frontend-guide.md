# Scheduled Courses — Frontend Build Guide (Instructor)

A complete, self-contained reference for building the **instructor / course-owner** UI for
**scheduled (cohort-based) courses**. Covers what the feature is, the authoring workflow, every
instructor-facing endpoint with request bodies and response examples, the schedule state machine, and
the error contract. You should be able to build the instructor side from this document without reading
backend source.

> **Scope:** instructor / course-owner side only. The learner side (cohort enrollment, curriculum
> consumption, release gates) is **out of scope for now** — see §9 for the boundary. Backend design:
> `docs/architecture/22-scheduled-courses.md`, `23-scheduled-course-lifecycle.md`. Manual tests:
> `docs/api-testing/postman-schedules.md`.

---

## 1. What the feature is

Normally a course is **self-paced**: authored once, published, available forever, all content unlocked
immediately.

A **scheduled course** runs like a university semester:

- Learners sign up during a fixed **enrollment window**.
- The course **starts on a date**. Before that, content is hidden.
- Material is released **week by week** ("drip") — sections carry an unlock date.
- There may be a **seat cap** per cohort.

Mechanism: the course stays the reusable curriculum template. A separate **`CourseSchedule`** object
wraps it with the dates, seat cap, and status. One course can have **many** schedules (re-runs).

```
NidusCourse (curriculum: sections, lectures, quizzes …)
   ├── CourseSchedule "Fall 2026 Batch"   (dates + seats + status)
   └── CourseSchedule "Spring 2027 Batch"
```

Delivery mode is chosen **at creation and immutable afterward**:

| `delivery_mode` | Meaning |
|---|---|
| `self_paced` | Classic evergreen course. No schedules. |
| `scheduled` | Cohort-based. Requires ≥1 schedule + a `course_outline` before it can be submitted for review. |

---

## 2. Who uses the instructor UI

| Role | Can | Auth |
|---|---|---|
| **Individual instructor** | Own the course + schedules fully | `IsVerifiedCourseCreator` |
| **Partner institution** (institution-owned course) | Own the course + schedules fully | `IsVerifiedCourseCreator` |
| **Roster expert** (institution course) | **Read-only** schedule visibility; authors content | rostered on course |

Institution-owned course → only the institution mutates schedules; the rostered expert can *see* the
timeline but every mutation (create/edit/delete/transition) returns **404** for them.

---

## 3. Schedule state machine

```
draft ──activate──▶ scheduled ──(start_date passes, auto)──▶ ongoing ──(end_date passes, auto)──▶ completed ──archive──▶ archived
  ▲                    │                                                                                               │
  └────── rework ──────┘◀──────────────────────────────── rework ──────────────────────────────────────────────────┘
```

| Transition | Trigger | UI control |
|---|---|---|
| `draft → scheduled` | Owner | "Activate" → `POST .../activate/` |
| `scheduled → draft` | Owner | "Rework" (pull back premature activation) |
| `scheduled → ongoing` | **Automatic** (Celery beat, ~5 min after `start_date`) | none — refresh to reflect |
| `ongoing → completed` | **Automatic** (after `end_date`; null end = stays ongoing) | none |
| `completed → archived` | Owner | "Archive" |
| `archived → draft` | Owner | "Rework" (reuse row for another run) |

**Editability:** a schedule is PATCH-editable only while `draft` or `scheduled`; frozen once `ongoing`.
Deletable only while `draft`. Disable Edit/Delete controls per `status`.

Course status (`draft → under_review → published`) is unchanged by this feature — `delivery_mode:
scheduled` only changes what counts as complete enough to submit (§4 Step 4).

---

## 4. Authoring workflow — building & launching a cohort course

Real UI flow is **one sitting**: pick "Scheduled (Cohort-Based)" up front, fill course metadata + the
first cohort's dates, submit once. Admin approval is **not** skipped.

### Step 1 — Create the course as `scheduled`

```
POST /api/v1/courses/create/
Authorization: Bearer <owner_token>
Content-Type: application/json

{
  "title": "Full-Stack Bootcamp — Fall Cohort",
  "description": "12-week cohort-based bootcamp.",
  "price": "49.99",
  "language": "English",
  "level": "beginner",
  "category": 3,
  "delivery_mode": "scheduled",
  "course_outline": "Week 1: HTML/CSS\nWeek 2: JavaScript\nWeek 3: React\n..."
}
```

**201 Created**

```json
{
  "success": true,
  "message": "Course created.",
  "data": {
    "id": 42,
    "title": "Full-Stack Bootcamp — Fall Cohort",
    "slug": "full-stack-bootcamp-fall-cohort",
    "status": "draft",
    "delivery_mode": "scheduled",
    "course_outline": "Week 1: HTML/CSS\nWeek 2: JavaScript\n...",
    "price": "49.99",
    "...": "...standard course fields"
  }
}
```

- `delivery_mode` is **immutable** — later `PATCH /courses/42/` changing it → **400**
  `errors.delivery_mode`.
- `course_outline` is plain multi-line text. For a scheduled course it stands in for a fully-built
  curriculum so the admin can judge scope; **required (non-blank) before submission**.

### Step 2 — Attach a schedule (course still `draft`)

No "course must be published" requirement here — only activation has that.

```
POST /api/v1/courses/42/schedules/
Authorization: Bearer <owner_token>
Content-Type: application/json

{
  "cohort_label": "Fall 2026 Batch",
  "timezone": "Asia/Dhaka",
  "enrollment_opens_at": "2026-08-01T00:00:00Z",
  "enrollment_closes_at": "2026-08-31T23:59:59Z",
  "start_date": "2026-09-01T09:00:00Z",
  "end_date": "2026-12-15T00:00:00Z",
  "max_seats": 50
}
```

**Request field reference:**

| Field | Required | Notes |
|---|---|---|
| `cohort_label` | no | Human name, e.g. "Fall 2026 Batch". Blank allowed. |
| `timezone` | no | IANA name, default `"UTC"`. **Display only** — comparisons use the UTC datetimes. Not validated. |
| `enrollment_opens_at` | yes | ISO 8601 datetime. |
| `enrollment_closes_at` | yes | Must be **after** `enrollment_opens_at`. |
| `start_date` | yes | Must be **on or after** `enrollment_closes_at`. |
| `end_date` | no | `null` = open-ended (stays ongoing). If set, must be **after** `start_date`. |
| `max_seats` | no | `null` = unlimited. Positive integer. |

**201 Created**

```json
{
  "success": true,
  "message": "Schedule created.",
  "data": {
    "id": 12,
    "course": 42,
    "cohort_label": "Fall 2026 Batch",
    "timezone": "Asia/Dhaka",
    "enrollment_opens_at": "2026-08-01T00:00:00Z",
    "enrollment_closes_at": "2026-08-31T23:59:59Z",
    "start_date": "2026-09-01T09:00:00Z",
    "end_date": "2026-12-15T00:00:00Z",
    "max_seats": 50,
    "status": "draft",
    "created_by": { "id": 7, "full_name": "Jane Doe", "email": "jane@example.com" },
    "last_edited_by": { "id": 7, "full_name": "Jane Doe", "email": "jane@example.com" },
    "created_at": "2026-07-18T10:00:00Z",
    "updated_at": "2026-07-18T10:00:00Z"
  }
}
```

> **Invariant:** a schedule attaches only to a `delivery_mode: scheduled` course. On a self-paced
> course → **422** `"Schedules can only be added to scheduled (cohort-based) courses."`

### Step 3 — (Optional) author curriculum now, or later

Sections are **optional** at submission for a scheduled course (the `course_outline` covers scope). Add
week-1 content now, or drip it in once the cohort runs (§7).

```
POST /api/v1/courses/42/sections/create/
{ "title": "Week 1 — HTML/CSS", "position": 1 }
```

### Step 4 — Submit for review

```
POST /api/v1/courses/42/submit/
Authorization: Bearer <owner_token>
```

**Scheduled-course submit validation** (differs from self-paced):

| Check | Failure |
|---|---|
| ≥1 schedule attached | **400** `errors.schedules` — "A scheduled (cohort) course must have at least one schedule attached before it can be submitted for review." |
| `course_outline` non-blank | **400** `errors.course_outline` — "A scheduled (cohort) course must have a course outline before it can be submitted for review." |
| Sections | **not required** — may submit with zero sections. |

**200 OK** → `data.status = "under_review"`. Content is now frozen.

> Institution-owned courses use `POST /finish/` → institution `POST /institution-review/`
> `{"action":"submit"}` instead of `POST /submit/`. Downstream is identical.

### Step 5 — Admin approves → course publishes → schedule auto-activates

On admin approve, the course becomes `published` **and every `draft` schedule auto-activates**
(`draft → scheduled`). No separate activate call on the happy path.

```
GET /api/v1/courses/42/schedules/12/
→ data.status = "scheduled"
```

**Stale-dates fallback:** if `enrollment_closes_at` / `start_date` already passed by approval time, the
**course still publishes** but the schedule stays `draft` (activation fails silently; owner gets a
`COURSE_SCHEDULE_NEEDS_ATTENTION` notification). Recovery: PATCH dates into the future, then manually
activate (Step 6). Surface a **"needs attention"** badge on any `draft` schedule of a `published`
course.

### Step 6 — Manual activate (only if not auto-activated)

```
POST /api/v1/courses/42/schedules/12/activate/
```

**Activation preconditions** (all must hold, else **400** with field-keyed `errors`):

1. Course is `published` → `errors.course`
2. `enrollment_opens_at < enrollment_closes_at <= start_date`
3. `end_date > start_date` (when set)
4. `enrollment_closes_at` and `start_date` are in the **future**

**200 OK** → `data.status = "scheduled"`.

---

## 5. Schedule management endpoints (detail)

All under `/api/v1`. `<pk>` = course id, `<id>` = schedule id. Gated `IsVerifiedCourseCreator`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/courses/<pk>/schedules/` | List (paginated) — owner + roster expert |
| `POST` | `/courses/<pk>/schedules/` | Create — owner only |
| `GET` | `/courses/<pk>/schedules/<id>/` | Detail — owner + roster expert |
| `PATCH` | `/courses/<pk>/schedules/<id>/` | Edit — owner, draft\|scheduled only |
| `DELETE` | `/courses/<pk>/schedules/<id>/` | Delete — owner, draft only |
| `POST` | `/courses/<pk>/schedules/<id>/activate/` | draft → scheduled |
| `POST` | `/courses/<pk>/schedules/<id>/rework/` | scheduled\|archived → draft |
| `POST` | `/courses/<pk>/schedules/<id>/archive/` | completed → archived |

### List schedules — paginated

```
GET /api/v1/courses/42/schedules/
```

**200 OK** (standard paginated envelope, newest-first):

```json
{
  "success": true,
  "data": {
    "count": 2,
    "next": null,
    "previous": null,
    "results": [
      { "id": 12, "cohort_label": "Fall 2026 Batch", "status": "scheduled", "start_date": "2026-09-01T09:00:00Z", "max_seats": 50, "...": "..." },
      { "id": 8,  "cohort_label": "Summer 2026 Batch", "status": "completed", "...": "..." }
    ]
  }
}
```

Roster experts get read access; mutations return 404 for them.

### Get one schedule

```
GET /api/v1/courses/42/schedules/12/
→ 200, data = full CourseSchedule object (same shape as the create response)
```

### Edit (draft | scheduled only)

```
PATCH /api/v1/courses/42/schedules/12/
{ "max_seats": 60 }
```

- **200** while `draft`/`scheduled`.
- **422** once `ongoing`/`completed`/`archived`: `"This schedule is \"ongoing\" and cannot be edited.
  Only draft or scheduled cohorts can be modified."`
- Bad date ordering → **400**, e.g. `errors.enrollment_opens_at` = "Enrollment must open before it
  closes."

Writable fields: `cohort_label`, `timezone`, `enrollment_opens_at`, `enrollment_closes_at`,
`start_date`, `end_date`, `max_seats`. `status` moves **only** through transition endpoints; `course`
is fixed by the URL.

### Delete (draft only)

```
DELETE /api/v1/courses/42/schedules/12/
```

- **200** while `draft`.
- **422** otherwise: `"Only draft schedules can be deleted."`

### Transitions

Each returns **200** with the updated schedule in `data`.

```
POST .../schedules/12/activate/   draft → scheduled            ("Schedule activated.")
POST .../schedules/12/rework/      scheduled|archived → draft   ("Schedule moved back to draft.")
POST .../schedules/12/archive/     completed → archived         ("Schedule archived.")
```

Illegal transition (e.g. archive a `draft`) → **422** plain message:
`"Cannot transition from \"draft\" to \"archived\". Allowed: scheduled."`

---

## 6. Drip authoring — releasing content week by week

Each section can carry an optional **`unlocks_at`** datetime (the drip lock):

- `unlocks_at = null` / omitted → released immediately.
- `unlocks_at` in the future → locked for learners until that moment.

Set it through the normal section create/update endpoints:

```
POST /api/v1/courses/42/sections/create/
Authorization: Bearer <owner_token>

{ "title": "Week 2 — Advanced Topics", "position": 2, "unlocks_at": "2026-09-08T09:00:00Z" }
```

**Editable fields on a section:** `title`, `description`, `position`, `unlocks_at`.

> **Drip authoring window:** a `published` course is normally frozen, but a published course **with an
> `ongoing` schedule** is content-editable — instructors upload week-2 material mid-cohort. The window
> closes when every schedule reaches `completed`. Adding week-N content does **not** re-trigger admin
> review; authorship is stamped (`created_by`/`last_edited_by`) for the audit trail.

Instructor implication: on a published scheduled course, the "add section / add lecture" controls
should be **enabled while any schedule is `ongoing`** and disabled otherwise (before start / after all
complete → editing returns **422** "course is published and cannot be edited").

---

## 7. Error contract summary

Envelope: `{ "success": bool, "message": string, ... }`.

| Status | When | Body |
|---|---|---|
| **400** | Field validation (bad dates, missing outline/schedule on submit, changing `delivery_mode`) | `{ success:false, message, errors:{field:[...]} }` |
| **403** | Wrong user type on schedule endpoints (learner / unverified) | `{ success:false, message }` |
| **404** | No access via numeric id (roster expert mutating, cross-tenant), or unknown id | `{ success:false, message:"Course not found." / "Schedule not found." }` — existence never leaked |
| **422** | Business rule (edit frozen schedule, delete non-draft, illegal transition, schedule on self-paced course) | `{ success:false, message }` (no `errors` key) |

**UI distinction:**

- **400** = form data wrong → inline field errors from `errors`.
- **422** = action not allowed in this state → toast/banner with `message`.
- **404** on a schedule you expected → usually **no access** (roster expert / cross-tenant), not deleted.

---

## 8. Frontend checklist (instructor)

- [ ] Course create form: delivery-mode toggle (`self_paced` / `scheduled`), disabled after creation.
- [ ] For `scheduled`: `course_outline` textarea (required) + schedule-builder before submit.
- [ ] Schedule form: dates with client-side ordering hints mirroring §4 Step 2; timezone picker (display only).
- [ ] Schedule list with per-row status badge; disable Edit/Delete per editability rules (§3).
- [ ] "Needs attention" badge on a `draft` schedule of a `published` course (stale-dates fallback).
- [ ] Activate / Rework / Archive buttons gated by current status.
- [ ] Section editor: `unlocks_at` picker; enable add/edit controls only while a schedule is `ongoing` on a published course.
- [ ] Distinguish 400 (inline field errors) vs 422 (state banner) vs 404 (not permitted).

---

## 9. Out of scope (learner side, not built into this UI yet)

For reference only — not part of the instructor UI now:

- **Cohort enrollment** — `POST /courses/<slug>/enroll/` with optional `{"schedule_id": N}`; window /
  capacity / paid-course gates.
- **Learner curriculum + release gates** — `GET /courses/learn/<slug>/curriculum/` returns
  `is_locked` / `unlocks_at` per section; locked or pre-start content → **422** on detail/write
  endpoints.
- **Lifetime access after end date** — nothing revoked when a cohort completes.

When the learner UI is built, see `docs/architecture/22-scheduled-courses.md` §6–9 and
`docs/api-testing/postman-schedules.md` Groups 3–6.
