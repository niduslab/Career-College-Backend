# Postman Guide — Expert Editing Institution-Assigned Courses

How an **institution-onboarded expert** logs in and edits a course the partner institution created and
assigned them to. This is the consumer side of the partner-institution roster flow — for the institution
side (verification, onboarding experts, creating the course, assigning the roster) see
[postman-partner-institution.md](postman-partner-institution.md).

Architecture reference: [docs/architecture/18-partner-institutions.md](../architecture/18-partner-institutions.md).

---

## The flow in one picture

```
Institution (created_by)                       Expert (in course.instructors)
─────────────────────────                      ──────────────────────────────
provision_expert  ─── credentials email ──────► log in (email + preset password) → JWT
create course (draft, partner_institution set)
assign expert  ── POST {pk}/institution-instructors/ ──► expert now in course.instructors
                                                 GET courses/                 → sees the course
                                                 GET courses/{pk}/            → opens it
                                                 build curriculum (sections, lectures, quizzes, …)
                                                 POST courses/{pk}/finish/    → institution_review
POST courses/{pk}/institution-review/  {submit}  ── ► under_review (platform admin)
   ─ or ─  {send_back, rejection_reason}          ── ► rejected → expert reworks → finish again
```

> **Two-stage submission (institution courses only).** An institution-owned course does **not** go straight to the platform admin. The expert hits `/finish/` (→ `institution_review`); the institution then either **submits** it onward to the admin (→ `under_review`) or **sends it back** to the expert (→ `rejected`). Individual-instructor courses keep the direct `/submit/ → under_review` path. See [INSTITUTION_COURSE_SUBMISSION_FLOW.md](../future_implementations/INSTITUTION_COURSE_SUBMISSION_FLOW.md).

## Access rules (what makes this work)

- The expert account is `user_type='instructor'`, `is_verified=True`, `is_email_verified=True` —
  created by the institution; **no OTP / identity-verification step**.
- Every course **authoring** endpoint scopes by `instructors=request.user`
  (e.g. `NidusCourse.objects.get(pk=..., instructors=request.user)`). Assigning the expert runs
  `course.instructors.add(expert)`, so the expert immediately passes that filter and can edit content.
- Authoring permission is `IsCourseCreator` (instructor **or** partner institution — identity
  verification **not** required) on course-level views; section/content views use `IsAuthenticated`
  + the `instructors=request.user` queryset filter; coding/assignment views use `IsInstructorUser`
  (verification not required either). See **Known gap** below.
- Editing is only allowed while the course `is_editable()` → status in **`draft`** or **`rejected`**.
  Once `institution_review` / `under_review` / `published` / `archived`, content writes return **422**.
- **Submission is two-stage for institution courses.** The expert uses `/finish/` (not `/submit/`);
  `/submit/` on an institution course → **422**. The institution forwards/sends-back via
  `/institution-review/`. Individual-instructor courses still use `/submit/` directly.
- The expert is **not** a partner institution → cannot manage the roster
  (`POST/DELETE …/institution-instructors/` → **403**).
- Numeric course id → **404** on no-access (never leak existence). A course the expert isn't assigned
  to, or was removed from, simply 404s.

---

## Environment variables

| Variable | Value | Notes |
|----------|-------|-------|
| `base_url` | `http://127.0.0.1:8000` | |
| `institution_token` | _(from institution login)_ | Used only for the prerequisite setup steps |
| `expert_email` | _(filled during setup)_ | The onboarded expert's login email |
| `expert_password` | _(from credentials email)_ | Preset password emailed on onboarding |
| `expert_token` | _(filled at login)_ | Expert's JWT access token |
| `expert_user_id` | _(filled during setup)_ | The expert's **User** id (for roster assignment) |
| `course_id` | _(filled during setup)_ | A `draft` course owned by the institution |
| `section_id` | _(filled during tests)_ | |
| `content_id` | _(filled during tests)_ | `SectionContent.id` for reorder |

Auth header for every expert call: `Authorization: Bearer {{expert_token}}`.

---

## Group 0: Prerequisites (institution side — quick setup)

Done with `{{institution_token}}` (a **verified** partner institution). See the partner-institution
guide for full detail; the minimum here:

1. **Onboard an expert** — `POST {{base_url}}/api/v1/auth/partner/experts/`
   ```json
   { "full_name": "Dr. Ada Expert", "email": "ada.expert@example.com" }
   ```
   `201`. Save `data.user.id` → `expert_user_id`, `data.user.email` → `expert_email`. The preset
   password is in the credentials email (console backend prints it in dev) → `expert_password`.

2. **Create a course** — `POST {{base_url}}/api/v1/courses/create/`
   ```json
   { "title": "Intro to Distributed Systems", "description": "...", "category": 1, "level": "beginner", "price": "0.00" }
   ```
   `201`. Status is `draft`, `partner_institution` set automatically. Save `data.id` → `course_id`.

3. **Assign the expert** — `POST {{base_url}}/api/v1/courses/{{course_id}}/institution-instructors/`
   ```json
   { "expert_user_id": {{expert_user_id}} }
   ```
   **Expected:** `200 OK`, `message: "Expert assigned to course."`

---

## Group 1: Expert logs in

### 1.1 Login — happy path

`POST {{base_url}}/api/v1/auth/login/`
```json
{ "email": "{{expert_email}}", "password": "{{expert_password}}" }
```
**Expected:** `200 OK` with `access` + `refresh` tokens. Save `access` → `expert_token`.
No OTP step — the account is already email-verified.

---

## Group 2: Expert sees the assigned course

### 2.1 List my courses

`GET {{base_url}}/api/v1/courses/` — header `Authorization: Bearer {{expert_token}}`

**Expected:** `200 OK`, paginated. The assigned course (`course_id`) appears even though the expert is
**not** `created_by` — they're in `instructors`.

### 2.2 Course detail (authoring surface)

`GET {{base_url}}/api/v1/courses/{{course_id}}/`

**Expected:** `200 OK`, full authoring representation. `partner_institution` is populated; the expert is
listed among `instructors`.

### 2.3 A course the expert is NOT assigned to → 404

`GET {{base_url}}/api/v1/courses/999999/`

**Expected:** `404 Not Found`, `message: "Course not found."` (existence not leaked).

---

## Group 3: Expert builds the curriculum

All while the course is `draft` (editable).

### 3.1 Create a section

`POST {{base_url}}/api/v1/courses/{{course_id}}/sections/create/`
```json
{ "title": "Module 1 — Foundations", "position": 1 }
```
**Expected:** `201 Created`. Save `data.id` → `section_id`.

### 3.2 Add a content item (article lecture)

`POST {{base_url}}/api/v1/courses/sections/{{section_id}}/contents/`
```json
{ "item_type": "lecture", "title": "What is a distributed system?", "lecture_type": "article", "article_content": "<p>…</p>" }
```
**Expected:** `201 Created`. Response carries the new `content_id` (`SectionContent.id`) → save it.
(For `quiz` / `assignment` / `coding`, change `item_type` and the type-specific fields — same endpoint.)

### 3.3 Reorder a content item

`PATCH {{base_url}}/api/v1/courses/contents/{{content_id}}/reorder/`
```json
{ "position": 2 }
```
**Expected:** `200 OK`.

### 3.4 Patch course metadata

`PATCH {{base_url}}/api/v1/courses/{{course_id}}/`
```json
{ "description": "Updated by the assigned expert." }
```
**Expected:** `200 OK`. (The `instructors` field in a PATCH body is silently ignored — roster is
institution-owned.)

> The expert uses the **same** authoring endpoints documented for instructors: `/sections/`,
> `/lectures/`, `/quizzes/`, `/assignments/`, `/coding-exercises/`. They all scope by
> `instructors=request.user`, so the assigned expert has full read/write on this course's content.

---

## Group 4: Two-stage submission

### 4.1 Expert marks the course finished

`POST {{base_url}}/api/v1/courses/{{course_id}}/finish/` — header `Bearer {{expert_token}}`

**Expected:** `200 OK` on a complete course → status `draft → institution_review`.
- `400` if completeness checks fail (missing title/description, empty section, video not `ready`, quiz
  without a correct answer, …) — `errors` payload lists what's missing.
- `422` if the course is **not** institution-owned (individual courses use `/submit/`).
- `422` for an invalid transition (e.g. already finished).

The course is now **frozen** (content edits → 422) and waiting for the institution.

### 4.2 Expert calling `/submit/` on an institution course → 422

`POST {{base_url}}/api/v1/courses/{{course_id}}/submit/`

**Expected:** `422 Unprocessable Entity` — institution courses must go through `/finish/`, not `/submit/`.

### 4.3 Institution submits to the platform admin

Switch to `{{institution_token}}`.

`POST {{base_url}}/api/v1/courses/{{course_id}}/institution-review/`
```json
{ "action": "submit" }
```
**Expected:** `200 OK` → status `institution_review → under_review`. Platform admins are notified
(`COURSE_SUBMITTED`). The course now follows the normal admin review (`/review/` → published/rejected).

### 4.4 Institution sends the course back instead

(Alternative to 4.3 — run on a fresh course in `institution_review`.)

`POST {{base_url}}/api/v1/courses/{{course_id}}/institution-review/`
```json
{ "action": "send_back", "rejection_reason": "Module 2 needs more depth." }
```
**Expected:** `200 OK` → status `institution_review → rejected`. The expert is notified
(`COURSE_SENT_BACK`). The expert reworks via `POST {pk}/rework/` (`rejected → draft`), edits, and calls
`/finish/` again.
- `400` if `rejection_reason` is missing.
- `422` if the course is not in `institution_review`.

---

## Group 5: Negative & edge cases

### 5.1 Editing a frozen course (in institution_review) → 422

After 4.1 the course is `institution_review`. Retry 3.1 as the expert:

`POST {{base_url}}/api/v1/courses/{{course_id}}/sections/create/`

**Expected:** `422 Unprocessable Entity` — course not editable (only `draft`/`rejected` are).

### 5.2 Expert cannot run institution review → 403

`POST {{base_url}}/api/v1/courses/{{course_id}}/institution-review/` (as `{{expert_token}}`)
```json
{ "action": "submit" }
```
**Expected:** `403 Forbidden` — requires `IsVerifiedPartnerInstitution`; the expert is an instructor.

### 5.3 Institution user cannot mark finished → 404

`POST {{base_url}}/api/v1/courses/{{course_id}}/finish/` (as `{{institution_token}}`)

**Expected:** `404 Not Found` — `/finish/` is scoped to `instructors`; the institution is `created_by`,
not in `instructors`, so the row isn't found (existence not leaked).

### 5.4 Another institution cannot act on this course → 404

`POST {{base_url}}/api/v1/courses/{{course_id}}/institution-review/` with a **different** institution's
token → **404** (scoped to the owning institution).

### 5.5 Expert cannot manage the roster → 403

`POST {{base_url}}/api/v1/courses/{{course_id}}/institution-instructors/`
```json
{ "expert_user_id": {{expert_user_id}} }
```
**Expected:** `403 Forbidden` — requires `IsVerifiedPartnerInstitution`; the expert is an instructor.

### 5.6 After the institution removes the expert → 404

Institution runs (with `{{institution_token}}`):
`DELETE {{base_url}}/api/v1/courses/{{course_id}}/institution-instructors/{{expert_user_id}}/` → `200`.

Now the expert retries 2.2:
`GET {{base_url}}/api/v1/courses/{{course_id}}/`

**Expected:** `404 Not Found` — the expert is no longer in `instructors`, so the authoring queryset
filter excludes the course. Previously authored content is retained on the course; only the expert's
access is revoked.

### 5.7 Deactivated expert — KNOWN GAP, does not currently block authoring

> **Known gap, unresolved.** This section previously documented "deactivated expert can no longer
> author" as a passing test. That's no longer true — see below. Do not rely on this as a security
> boundary until it's fixed.

If the institution deactivates the expert (`PATCH /api/v1/auth/partner/experts/{id}/` with
`is_active=false`), `InstructorProfile.is_verified` flips to `False` and `affiliation_status`
flips to `removed` — but `set_expert_active()` does **not** remove the expert from
`course.instructors`, and course/section/content/coding/assignment authoring endpoints no longer
check `is_verified` at all (`IsCourseCreator` / `IsInstructorUser`). Retrying the expert's
authoring calls (course detail, add section, add content, coding exercises, assignments) on a
course they're still rostered on currently returns the **same success responses as before
deactivation** — not the `403` this section used to assert.

---

## Quick reference — endpoints the expert uses

| Step | Method | Endpoint | Permission |
|------|--------|----------|------------|
| Login | POST | `/api/v1/auth/login/` | public |
| List my courses | GET | `/api/v1/courses/` | `IsCourseCreator` |
| Course detail | GET/PATCH | `/api/v1/courses/{pk}/` | `IsCourseCreator` + `instructors` filter |
| Create section | POST | `/api/v1/courses/{course_id}/sections/create/` | `instructors` filter |
| Add content | POST | `/api/v1/courses/sections/{section_id}/contents/` | `instructors` filter |
| Reorder content | PATCH | `/api/v1/courses/contents/{content_id}/reorder/` | `instructors` filter |
| **Mark finished** (expert) | POST | `/api/v1/courses/{pk}/finish/` | `instructors` filter; institution course → `institution_review` |
| **Institution review** | POST | `/api/v1/courses/{pk}/institution-review/` `{action: submit\|send_back}` | `IsVerifiedPartnerInstitution`, owning institution → **403 for expert** |
| Submit for review (individual only) | POST | `/api/v1/courses/{pk}/submit/` | owner/instructor; institution course → **422** |
| Manage roster | POST/DELETE | `/api/v1/courses/{pk}/institution-instructors/…` | `IsVerifiedPartnerInstitution` → **403 for expert** |
