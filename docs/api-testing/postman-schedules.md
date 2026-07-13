# Postman Guide — Scheduled Courses (Cohorts · Drip Release · Cohort Enrollment)

Manual API testing for the scheduled-courses slice. A **schedule** is a cohort run of an existing
course: an enrollment window, a start (and optional end) date, and an optional seat cap, layered on
top of the normal course without touching the course review flow. The course owner (individual
instructor, or the institution for institution-owned courses) creates and activates schedules; a
Celery-beat task flips them `scheduled → ongoing → completed` as the dates pass; learners enroll
into a cohort through the ordinary enroll endpoint with a `schedule_id`; sections with a future
`unlocks_at` stay locked for learners until release ("drip").

Flow under test:

1. **Schedule authoring** — owner creates / edits / activates a schedule on a published course.
2. **Ownership** — institution-only mutation on institution courses; roster experts read-only.
3. **Cohort enrollment** — learner joins inside the window; window/capacity refusals.
4. **Drip authoring** — owner adds a week-2 section (with `unlocks_at`) while the cohort is ongoing.
5. **Learner gates** — locked sections marked in the curriculum; locked/pre-start content → 422.

> **Prerequisite feature:** you need a **published** course. For institution-owned courses complete
> the partner-institution setup first (`postman-partner-institution.md`); for an individual course a
> verified instructor + the normal submit/approve flow (`postman docs for courses`) is enough. See
> `docs/architecture/22-scheduled-courses.md` for the design.

> **Celery beat:** the automatic `scheduled → ongoing → completed` transitions run via
> `advance_course_schedules_task` (every 5 min). For manual testing you don't have to wait — the
> walkthrough below tells you where to fake the dates instead.

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `instructor_token` | `Bearer eyJ...` | Verified individual instructor owning `course_pk` |
| `institution_token` | `Bearer eyJ...` | Verified partner institution (institution-course checks) |
| `other_institution_token` | `Bearer eyJ...` | A second institution (cross-tenant 404 checks) |
| `expert_token` | `Bearer eyJ...` | Active affiliated expert rostered on the institution course |
| `learner_token` | `Bearer eyJ...` | Any learner |
| `learner2_token` | `Bearer eyJ...` | Second learner (capacity test) |
| `course_pk` / `course_slug` | _(filled during tests)_ | A **published** course owned by `instructor_token` |
| `inst_course_pk` | _(filled during tests)_ | A published institution-owned course |
| `schedule_id` | _(filled during tests)_ | PK of the schedule under test |
| `locked_lecture_id` / `open_lecture_id` | _(filled during tests)_ | Lectures inside the locked / released sections |

---

## Access-Denied / Refusal Policy (applies throughout)

| Case | Response |
|---|---|
| Schedule URLs (`<pk>/schedules/...`, all numeric IDs) with no access | **404**, body message `"Course not found."` — never leaks existence |
| Learner / unverified caller on schedule endpoints | **403** (permission class) |
| Business-rule refusal (patch an `ongoing` schedule, delete a non-draft, archive a draft) | **422** |
| Enrollment-window / capacity refusal | **422** |
| Locked or pre-start content for an enrolled learner | **422** (not 403/404 — the learner has access; it's a timing rule) |

---

## Group 0: Full Lifecycle — Create Course + Schedule → Submit → Admin Approval

**Real frontend flow, not two separate journeys.** A user building a cohort course doesn't create a
plain course in one screen and come back later to "add scheduling" — they pick "Scheduled
(Cohort-Based)" up front and fill in course metadata + the cohort dates in the same sitting, then
submit once. So this walkthrough calls `POST /create/` immediately followed by
`POST /<pk>/schedules/` on the still-`draft` course — schedule creation has **no** "course must be
published" requirement; only `/activate/` does (see 0.7). Individual-instructor path shown
(`instructor_token` + `POST /submit/`); institution-owned swaps in `institution_token`/`expert_token`
and goes through `POST /finish/` → `POST /institution-review/` `{"action":"submit"}` before admin
review — the admin step (0.6–0.8) is identical either way. Full narrative:
[23-scheduled-course-lifecycle.md](../architecture/23-scheduled-course-lifecycle.md).

> **Important — admin approval is NOT skipped.** `delivery_mode: "scheduled"` changes only what
> counts as "complete enough to submit" (a written `course_outline` instead of built-out sections —
> see 0.4/0.5). It does **not** bypass the normal `draft → under_review → published` state machine.
> An admin (or, for institution-owned courses, the institution first) still has to approve every
> scheduled course exactly like a self-paced one. The one thing that *is* automatic post-approval is
> the schedule itself flipping `draft → scheduled` (0.8) — not the course's publish gate.

### 0.1 Create the course as `scheduled`

```
POST {{base_url}}/courses/create/
Authorization: {{instructor_token}}
Content-Type: application/json

{
    "title": "Full-Stack Bootcamp — Fall Cohort",
    "description": "12-week cohort-based bootcamp.",
    "price": "49.99",
    "language": "English",
    "level": "beginner",
    "category": {{category_id}},
    "delivery_mode": "scheduled",
    "course_outline": "Week 1: HTML/CSS\nWeek 2: JavaScript\nWeek 3: React\n... (full plan)"
}
```

**Expect 201.** `data.status = "draft"`, `data.delivery_mode = "scheduled"`. Save `data.id` →
`course_pk`. `delivery_mode` is **immutable after creation** — a later `PATCH .../{{course_pk}}/`
attempting to change it → **400** `errors.delivery_mode`.

### 0.2 Attach the schedule — same sitting, course still `draft`

```
POST {{base_url}}/courses/{{course_pk}}/schedules/
Authorization: {{instructor_token}}

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

**Expect 201**, `data.status = "draft"`. Save `data.id` → `schedule_id`. Note the course is still
`draft` at this point — scheduling doesn't require (or trigger) publication.

### 0.3 (Optional) add curriculum now, or leave it for later

```
POST {{base_url}}/courses/{{course_pk}}/sections/create/
Authorization: {{instructor_token}}
{ "title": "Week 1 — HTML/CSS", "position": 1 }
```

**Expect 201.** Entirely optional for a scheduled course — skip this and go straight to 0.5 to prove
the outline-only path works (see Group 4 for adding sections later, mid-cohort).

### 0.4 Negative: submit with no schedule attached → 400

On a **second** scratch course (`delivery_mode: "scheduled"`, `course_outline` filled, skip 0.2):

```
POST {{base_url}}/courses/{{scratch_course_pk}}/submit/
```

**Expect 400**, `errors.schedules` — `"A scheduled (cohort) course must have at least one schedule
attached before it can be submitted for review."`

### 0.5 Negative: submit with a blank `course_outline` → 400

On a **third** scratch course (`delivery_mode: "scheduled"`, `course_outline` omitted/blank, schedule
attached):

**Expect 400**, `errors.course_outline` — `"A scheduled (cohort) course must have a course outline
before it can be submitted for review."`

> Self-paced courses are unaffected by either check — they still require ≥1 section with content
> instead, and never require `course_outline`.

### 0.6 Submit for review

```
POST {{base_url}}/courses/{{course_pk}}/submit/
Authorization: {{instructor_token}}
```

**Expect 200**, `data.status = "under_review"`. (Content is now frozen, same as any submitted
course — `POST .../sections/create/` here → **422**.)

### 0.7 Admin finds the course in the review queue, then views context

```
GET {{base_url}}/courses/admin/pending-review/
Authorization: {{admin_token}}
```

**Expect 200**, standard paginated envelope; `results` includes `course_pk`, oldest-submitted-first.
This is the admin's only discovery surface for pending courses — there's no other listing, so this
is how an admin finds work without already knowing a course id from the `COURSE_SUBMITTED`
notification. Filter to just cohort courses with `?delivery_mode=scheduled` (or `self_paced`); an
unrecognized value → **400**.

```
GET {{base_url}}/courses/{{course_pk}}/review/
Authorization: {{admin_token}}
```

**Expect 200.** Response includes the normal course fields (incl. `course_outline`,
`delivery_mode: "scheduled"`) plus:

```json
{
  "schedules": [ { "id": ..., "cohort_label": "Fall 2026 Batch", "status": "draft", ... } ],
  "outline_stats": { "total_sections": 0, "sections_with_content": 0, "empty_section_titles": [] }
}
```

`outline_stats` is all-zero here if 0.3 was skipped — expected for a scheduled course that submitted
on outline alone. The admin's judgment call rests on `course_outline` + the schedule dates, exactly
the same approve/reject action as any other course:

### 0.8 Admin approves — course publishes, schedule auto-activates

```
POST {{base_url}}/courses/{{course_pk}}/review/
Authorization: {{admin_token}}
Content-Type: application/json

{ "action": "approve" }
```

**Expect 200**, `data.status = "published"`. Then:

```
GET {{base_url}}/courses/{{course_pk}}/schedules/{{schedule_id}}/
```

**Expect `data.status = "scheduled"`** — the approval auto-ran `activate_schedule()` for every
`draft` schedule on the course, no separate `/activate/` call needed.

### 0.9 Stale-dates fallback — schedule doesn't auto-activate

Repeat 0.1–0.7 with a schedule whose `enrollment_closes_at` / `start_date` will have **already
passed** by the time you approve (e.g. shell-update the schedule's dates into the past right before
0.8, or simply wait past them). Then approve:

**Expect 200**, `data.status = "published"` — **the course still publishes**; publishing is never
blocked by a stale schedule. But `GET .../schedules/{{schedule_id}}/` → `data.status` is still
`"draft"` (activation failed silently server-side, logged, not raised to the caller). The instructor
(or institution owner, if institution-owned) receives a `COURSE_SCHEDULE_NEEDS_ATTENTION`
notification naming the stale schedule. Recovery: fix the dates via `PATCH .../schedules/{{schedule_id}}/`
then manually `POST .../schedules/{{schedule_id}}/activate/` (→ 1.5).

You now have exactly the state Group 1 assumes going in — a published course with an activated
schedule. Continue there, or skip to Group 3 (enrollment).

---

## Group 1: Schedule Authoring (course owner)

> Group 1 uses `instructor_token` on the instructor-owned `course_pk`. The same requests work with
> `institution_token` on `inst_course_pk`.

### 1.1 Create a schedule — happy path

```
POST {{base_url}}/courses/{{course_pk}}/schedules/
Authorization: {{instructor_token}}
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

**Expect 201.** `data.status = "draft"`, `data.created_by` is you. Save `data.id` → `schedule_id`.

### 1.2 List schedules

```
GET {{base_url}}/courses/{{course_pk}}/schedules/
```

**Expect 200**, standard paginated envelope (`data.count / next / previous / results`).

### 1.3 Patch while draft

```
PATCH {{base_url}}/courses/{{course_pk}}/schedules/{{schedule_id}}/
{ "max_seats": 60 }
```

**Expect 200.**

### 1.4 Bad date ordering → 400

```
PATCH .../schedules/{{schedule_id}}/
{ "enrollment_opens_at": "2026-09-30T00:00:00Z" }
```

**Expect 400** with `errors.enrollment_opens_at` ("Enrollment must open before it closes.").

### 1.5 Activate — happy path

```
POST {{base_url}}/courses/{{course_pk}}/schedules/{{schedule_id}}/activate/
```

**Expect 200**, `data.status = "scheduled"`. Requires: course `published`, dates ordered, close/start
in the future — otherwise **400** with a field-keyed `errors` object (e.g. `errors.course` when the
course isn't published).

### 1.6 Patch while `scheduled` still allowed / frozen once `ongoing`

- PATCH now (status `scheduled`) → **200** (dates fixable until start).
- To simulate a started cohort without waiting for beat: in Django shell,
  `CourseSchedule.objects.filter(pk=...).update(status='ongoing')`. Then PATCH → **422**.

### 1.7 Rework a premature activation

```
POST .../schedules/{{schedule_id}}/rework/      (from status "scheduled")
```

**Expect 200**, back to `draft`. (`rework` also serves `archived → draft`.)

### 1.8 Delete rules

- DELETE while `draft` → **200**, row gone.
- DELETE in any other status → **422** `"Only draft schedules can be deleted."`

### 1.9 Archive

Only valid from `completed` (set via shell or wait for beat past `end_date`) → **200**; from any
other status → **422**.

---

## Group 2: Ownership & Access

### 2.1 Roster expert — read-only

With `expert_token` on `inst_course_pk` (expert is on the course roster):

- `GET .../schedules/` and `GET .../schedules/<id>/` → **200**
- `POST` create / `PATCH` / `DELETE` / `POST .../activate/` → **404** (mutations are
  institution-only; the 404 hides nothing the expert can't already see, but keeps the mutation
  surface consistent with the numeric-ID policy)

### 2.2 Cross-tenant → 404 everywhere

With `other_institution_token` on `inst_course_pk`: every schedule request (GET included) → **404**,
message exactly `"Course not found."`.

### 2.3 Wrong user type → 403

`learner_token` on any schedule URL → **403** (`IsVerifiedCourseCreator`).

---

## Group 3: Cohort Enrollment (learner)

> Ensure the schedule is `scheduled` and *now* is inside its enrollment window (create one with
> `enrollment_opens_at` in the past and `enrollment_closes_at` in the future, then activate — note
> activation requires close/start in the future, so use e.g. opens = yesterday, closes = +5 days,
> start = +6 days).

### 3.1 Enroll into the cohort — happy path

```
POST {{base_url}}/courses/{{course_slug}}/enroll/
Authorization: {{learner_token}}
Content-Type: application/json

{ "schedule_id": {{schedule_id}} }
```

**Expect 201.** `data.schedule = {{schedule_id}}`. Omitting the body (or `schedule_id`) is the
classic self-paced enrollment — unchanged, `data.schedule = null`.

### 3.2 Refusals

| Case | Setup | Expect |
|---|---|---|
| Unknown `schedule_id` | any number not on this course | **404** `"Schedule not found."` |
| Draft schedule | don't activate | **422** `"Enrollment for this cohort is not open."` |
| Window not open yet / already closed | dates via shell | **422** same message |
| Cohort full | `max_seats: 1`, enroll `learner2_token` first | **422** `"This cohort is full."` |
| Already in this cohort | repeat 3.1 | **422** `"You are already enrolled in this course."` |

Paid courses: the price gate is unchanged — a learner without a PAID order gets the usual 422
checkout message *before* any schedule logic runs; after paying, 3.1 works as above.

---

## Group 4: Drip Authoring (content upload mid-run)

> Requires the schedule `ongoing` (shell-flip or wait for beat past `start_date`). The course is
> `published` — normally frozen; the ongoing cohort opens the carve-out.

### 4.1 Add the week-2 section with a future unlock

```
POST {{base_url}}/courses/{{course_pk}}/sections/create/
Authorization: {{instructor_token}}

{
    "title": "Week 2 — Advanced Topics",
    "position": 2,
    "unlocks_at": "2026-09-08T09:00:00Z"
}
```

**Expect 201** (would be **422** "course is published and cannot be edited" without an ongoing
schedule — verify by flipping the schedule to `completed` and retrying). Lectures/quizzes are then
added under the new section through the normal content endpoints, same carve-out.

`unlocks_at = null` (or omitted) → the section releases immediately.

---

## Group 5: Learner Release Gates

> Learner from 3.1 (cohort member), cohort `ongoing`, week-1 section has no `unlocks_at`, week-2
> section unlocks in the future. Fill `open_lecture_id` / `locked_lecture_id` from the two sections.

### 5.1 Curriculum shows locks, hides nothing

```
GET {{base_url}}/courses/learn/{{course_slug}}/curriculum/
Authorization: {{learner_token}}
```

**Expect 200.** Both sections listed; week-1 `is_locked: false`, week-2 `is_locked: true` with its
`unlocks_at`. (Structure is never hidden — only item detail is gated.)

### 5.2 Released content works

`GET .../learn/lectures/{{open_lecture_id}}/` → **200**.

### 5.3 Locked content → 422

- `GET .../learn/lectures/{{locked_lecture_id}}/` → **422**
  `"This content has not been released yet."`
- `POST .../learn/lectures/{{locked_lecture_id}}/progress/` with
  `{"watched_seconds": 0, "is_completed": true}` → **422** (writes are gated too — no progress
  farming on unreleased content). Quiz/assignment submit and coding run/submit behave identically.

### 5.4 Before the cohort starts, everything is gated

Shell-set the schedule back to `scheduled` with a future `start_date`. Then:

- Curriculum → **200**, every section `is_locked: true`.
- Any lecture detail → **422** `"This course has not started yet."`

### 5.5 Instructor preview bypass

Same URLs with `instructor_token` → **200** everywhere, curriculum all `is_locked: false`. Authors
must QA unreleased weeks.

### 5.6 Self-paced learners still respect drip

Enroll a learner *without* `schedule_id` (3.1 body empty): week-1 lecture → **200**; week-2 (locked)
lecture → **422**. The drip lock is content-level and applies to everyone; only the *pre-start* gate
is cohort-specific.

---

## Group 6: After the End Date

Shell-set `end_date` in the past (status `ongoing`) and run the beat task once
(`advance_course_schedules_task()` in shell, or wait ≤5 min with beat running):

- Schedule status → `completed`.
- The cohort learner still gets **200** on all released content — **lifetime access; nothing is
  revoked.** Progress and submissions keep working.
- Content editing is frozen again (4.1 now → **422**).
- Certificates: unchanged — completing 100% still auto-issues.
