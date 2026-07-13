# Postman Guide — Learner Journey: Catalog, Enrollment, Payment & Consumption

Standalone guide covering the full learner path: browsing the public
catalog, enrolling (free or paid), and consuming course content. Extracted
and updated from `POSTMAN_TESTING_GUIDE.md` §35–42, with the payment flow
for paid-course enrollment added (previously that guide's §36 predated the
payments app and is now stale on this point — this guide reflects current
behavior).

Companion docs: `docs/architecture/12-enrollment.md`, `docs/architecture/21-payments.md`,
`docs/api-testing/postman-payments.md`.

> Scope note: this guide covers self-paced courses only. Cohort/schedule
> enrollment and webinars are intentionally out of scope — see
> `docs/api-testing/postman-schedules.md` and `docs/api-testing/postman-webinars.md`
> / `docs/api-testing/postman-webinar-payment-flow.md` respectively.

## Table of Contents

- [1. Setup](#1-setup)
- [2. Public Catalog (No Auth)](#2-public-catalog-no-auth)
  - [2.1 Browse, Filter, and Sort the Catalog](#21-browse-filter-and-sort-the-catalog)
  - [2.2 Course Categories](#22-course-categories)
- [3. Learner — Enrollment & Dashboard](#3-learner--enrollment--dashboard)
  - [3.1 Free-Course Enrollment](#31-free-course-enrollment)
  - [3.2 Paid-Course Enrollment (Payment Required)](#32-paid-course-enrollment-payment-required)
  - [3.3 My Courses (Dashboard + Player Header)](#33-my-courses-dashboard--player-header)
- [4. Learner Consumption (`/learn/...`)](#4-learner-consumption-learn)
  - [4.1 Curriculum Outline](#41-curriculum-outline)
  - [4.2 Lecture Detail + Watch Progress](#42-lecture-detail--watch-progress)
  - [4.3 Quiz Detail + Submission](#43-quiz-detail--submission)
  - [4.4 Assignment Detail + Submission + Polling + Retry](#44-assignment-detail--submission--polling--retry)
  - [4.5 Coding Exercise Detail + Run + Submit + Polling + Retry](#45-coding-exercise-detail--run--submit--polling--retry)
- [5. End-to-End Quick Flow](#5-end-to-end-quick-flow)

---

## 1. Setup

**Base URL:**
```
http://127.0.0.1:8000/api/v1
```

**Postman environment variables:**
```text
base_url              = http://127.0.0.1:8000/api/v1
learner_token          = <fill after login as a verified learner>
learner2_token         = <a second verified learner — isolation checks>
instructor_token       = <any instructor JWT — negative authz test>
free_course_slug       = intro-python
paid_course_slug       = advanced-django
category_id            =
lecture_id             =
quiz_id                =
assignment_id          =
submission_id          =
exercise_id            =
task_id                =
tran_id                =
order_id               =
```

**Headers (authenticated requests):**
```http
Authorization: Bearer {{learner_token}}
Content-Type: application/json
```

Public endpoints (catalog browse/detail, categories list) need no `Authorization` header.

**Backend `.env` preconditions for the payment section:** real sandbox credentials
from https://developer.sslcommerz.com in `SSLCOMMERZ_STORE_ID` /
`SSLCOMMERZ_STORE_PASSWORD`, `SSLCOMMERZ_SANDBOX=True`, `BACKEND_URL=http://localhost:8000`.
On localhost the IPN never fires (no public URL) — the **success redirect**
runs the same `finalize_payment`, so the flow still completes end-to-end locally.

---

## 2. Public Catalog (No Auth)

### 2.1 Browse, Filter, and Sort the Catalog

#### 2.1.1 Browse the Catalog

**GET** `{{base_url}}/courses/catalog/`

No Authorization header needed.

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "count": 2,
        "next": null,
        "previous": null,
        "results": [
            {
                "id": 1,
                "title": "Python Backend Bootcamp",
                "slug": "python-backend-bootcamp",
                "description": "Build production APIs with Django and DRF.",
                "thumbnail": null,
                "price": "79.99",
                "language": "English",
                "level": "intermediate",
                "duration_minutes": 240,
                "instructors": [
                    { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" }
                ],
                "category": { "id": 1, "name": "Backend Development", "slug": "backend" },
                "published_at": "2026-05-10T09:00:00Z"
            }
        ]
    }
}
```

#### 2.1.2 Filter the Catalog

All params optional, AND-combined. Successful filtered responses use the same paginated shape as 2.1.1.

**Filter fields:**

| Param | Type / accepted values | Example |
|---|---|---|
| `category` | slug (single) — rolls a parent down to its children | `?category=programming` |
| `subcategory` | slug (single) — exact match | `?subcategory=python` |
| `level` | CSV of `beginner`, `intermediate`, `advanced` | `?level=beginner,intermediate` |
| `language` | CSV of language strings (case-insensitive) | `?language=english,bangla` |
| `price_type` | `free` or `paid` | `?price_type=free` |
| `price_min` / `price_max` | non-negative decimal | `?price_min=10&price_max=99.99` |
| `duration_min` / `duration_max` | non-negative integer (minutes) | `?duration_min=60&duration_max=240` |
| `search` | text — matches title, description, instructor full name | `?search=python` |
| `rating_min` | decimal 1.0–5.0 — minimum avg rating | `?rating_min=4.0` |
| `min_reviews` | integer ≥ 0 | `?min_reviews=10` |
| `page` / `page_size` | pagination (see §46 of `POSTMAN_TESTING_GUIDE.md`) | `?page=2&page_size=25` |

**Sort field** (`?sort=<key>`, one value at a time):

| Key | Effect |
|---|---|
| `relevance` | Title match rank desc, then newest. Default when `?search=` is present. |
| `newest` | `-published_at`. Default when no `?search=`. |
| `popularity` | Active enrollment count desc, then newest. |
| `price_asc` / `price_desc` | Price ascending / descending. |
| `rating` | Average rating descending, then newest. No-review courses sort last. |

Combining example (beginner, `programming-python` subcategory, under $50, English, by popularity):
```
{{base_url}}/courses/catalog/?category=programming&subcategory=python&level=beginner&price_max=50&language=english&sort=popularity
```

**Validation errors** (400, field-keyed):

| Bad input | Sample error |
|---|---|
| `?sort=cheapest` | `{"sort": ["Invalid sort \"cheapest\". Must be one of: newest, popularity, price_asc, price_desc, rating, relevance."]}` |
| `?level=expert` | `{"level": ["Invalid level(s): expert. Must be one of: advanced, beginner, intermediate."]}` |
| `?price_min=-10` | `{"price_min": ["Must be non-negative."]}` |
| `?duration_max=3.5` | `{"duration_max": ["\"3.5\" is not a valid integer."]}` |

#### 2.1.3 View a Single Course Detail (Catalog)

**GET** `{{base_url}}/courses/catalog/{{course_slug}}/`

No Authorization header needed.

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "id": 1,
        "title": "Python Backend Bootcamp",
        "slug": "python-backend-bootcamp",
        "description": "Build production APIs with Django and DRF.",
        "thumbnail": null,
        "price": "79.99",
        "language": "English",
        "level": "intermediate",
        "duration_minutes": 240,
        "instructors": [ { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" } ],
        "partner_institution": null,
        "category": { "id": 1, "name": "Backend Development", "slug": "backend" },
        "learning_objectives": "Build REST APIs with Django REST Framework.",
        "prerequisites": "Basic Python knowledge.",
        "audiences": "Developers who want to build backend APIs.",
        "total_sections": 5,
        "total_content_items": 20,
        "published_at": "2026-05-10T09:00:00Z"
    }
}
```

Includes the full curriculum outline tree (titles/durations) plus preview
lecture HLS URLs where `Lecture.is_preview=True` — this is the marketing/SEO
page, distinct from the learner-consumption endpoints in §4.

**Error — course not found or not published:** 404 — `{ "detail": "No NidusCourse matches the given query." }`

### 2.2 Course Categories

Categories are a 2-level taxonomy (`parent` → `children`). List is public;
create/update/delete are admin-only. Slug auto-generated from `name` when omitted.

#### 2.2.1 List Categories (public, nested tree, paginated)

**GET** `{{base_url}}/courses/categories/`

No Authorization header needed.

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "count": 1,
        "next": null,
        "previous": null,
        "results": [
            {
                "id": 1,
                "name": "Programming",
                "slug": "programming",
                "children": [
                    { "id": 2, "name": "Python", "slug": "python", "children": [] }
                ]
            }
        ]
    }
}
```

> Create/update/delete require `IsPlatformAdmin` — see `POSTMAN_TESTING_GUIDE.md` §35A for the admin-side requests. This guide only needs the public list to populate a catalog filter dropdown.

---

## 3. Learner — Enrollment & Dashboard

> All endpoints in this section require `Authorization: Bearer {{learner_token}}` and a **learner** account with a verified email (course-owning instructors may also call the my-courses detail endpoint, for preview — see the [instructor course-preview guide]).

### 3.1 Free-Course Enrollment

**POST** `{{base_url}}/courses/{{free_course_slug}}/enroll/`

**Body:** *(empty)*

**Expected 201:**
```json
{
    "success": true,
    "message": "Enrolled successfully.",
    "data": {
        "id": 10,
        "course": {
            "id": 1, "title": "Intro to Python", "slug": "intro-python",
            "description": "...", "thumbnail": null, "price": "0.00",
            "language": "English", "level": "beginner", "duration_minutes": 180,
            "instructors": [ { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" } ],
            "category": { "id": 1, "name": "Backend Development", "slug": "backend" },
            "published_at": "2026-05-10T09:00:00Z"
        },
        "enrollment_type": "free",
        "is_active": true,
        "progress_percent": 0,
        "completed_at": null,
        "last_accessed_at": null,
        "created_at": "2026-05-13T10:30:00Z"
    }
}
```

#### Unenroll / Re-enroll

**POST** `{{base_url}}/courses/{{free_course_slug}}/unenroll/` → `is_active: false`, progress preserved.
**POST** `{{base_url}}/courses/{{free_course_slug}}/enroll/` again → same enrollment `id` reactivated, `is_active: true`.

#### Enrollment Error Cases

| Scenario | Status | Body |
|---|---|---|
| Enroll twice while already enrolled | 422 | `"You are already enrolled in this course."` |
| Unenroll without ever enrolling | 422 | `"You are not currently enrolled in this course."` |
| Non-learner (instructor) tries to enroll | 403 | `"Only learners can access this resource."` |
| Unverified learner | 403 | `"Email address is not verified."` |
| Enroll in a non-published course (draft/rejected slug) | 404 | `"No NidusCourse matches the given query."` |
| Unauthenticated | 401 | `"Authentication credentials were not provided."` |

### 3.2 Paid-Course Enrollment (Payment Required)

`POST /{{course_slug}}/enroll/` **rejects** any course with `price > 0` unless the caller
already holds a `PAID` `Order` for it. Trying to enroll directly:

```
POST {{base_url}}/courses/{{paid_course_slug}}/enroll/
Authorization: {{learner_token}}
```

**Expected 422:**
```json
{
    "success": false,
    "message": "This is a paid course. Complete payment via the checkout endpoint to enroll."
}
```

Payment must go through the SSLCommerz checkout flow first — see below.

#### 3.2.1 Open a Checkout Session

**POST** `{{base_url}}/payments/checkout/`

```json
{ "course_slug": "{{paid_course_slug}}" }
```

**Expected 201:**
```json
{
    "success": true,
    "message": "Checkout session created.",
    "data": {
        "gateway_url": "https://sandbox.sslcommerz.com/gwprocess/v4/gw.php?Q=PAY&SESSIONKEY=...",
        "order_id": 1,
        "tran_id": "CC9F2A...",
        "item_type": "course",
        "amount": "49.00",
        "currency": "BDT"
    }
}
```

Save `order_id` and `tran_id`.

**Negative checks:**

| Request | Expect |
|---|---|
| No auth | 401 |
| `instructor_token` | 403 (learner-only) |
| Empty body / missing `course_slug` | 400 |
| `{"course_slug": "{{free_course_slug}}"}` | 422 — `"This course is free. Use the enroll endpoint instead."` |
| Already actively enrolled | 422 — `"You are already enrolled in this course."` |
| Already purchased (`paid` order exists) | 422 pointing at the enroll endpoint |
| Unknown / unpublished slug | 404 |

**Re-checkout:** POST the same body twice without paying — the second response
has a **new** `tran_id`; the first order flips to `cancelled`. Only the newest
gateway session is live.

#### 3.2.2 Pay on the Gateway Page

Open `gateway_url` from a browser (not Postman's body). Sandbox test card:
**4111 1111 1111 1111**, any future expiry, any CVV, OTP `111111`. On completion
the browser lands on the backend `success/` callback → 302 redirect to
`{FRONTEND_URL}/payment/success?tran_id=...`.

#### 3.2.3 Verify the Outcome

1. **Order paid** — `GET {{base_url}}/payments/orders/` → the row for your `tran_id` has `"status": "paid"`, `paid_at` set, `item_type: "course"`.
2. **Enrollment granted** — `GET {{base_url}}/courses/my-courses/` → course present, `enrollment_type: "paid"`.
3. **Notification** — `GET {{base_url}}/notifications/` → a `payment.successful` row.

No separate `POST /enroll/` call is needed — `finalize_payment` grants the
enrollment directly inside the same transaction as the PAID write.

#### 3.2.4 Re-enroll After Purchase (No Double Charge)

Now that a PAID order exists, the direct enroll endpoint honors it:

```
POST {{base_url}}/courses/{{paid_course_slug}}/unenroll/
POST {{base_url}}/courses/{{paid_course_slug}}/enroll/
```

**Expected 201** on the re-enroll — `enrollment_type: "paid"`, reactivated. `GET /payments/orders/` still shows exactly **one** order — no second charge.

#### 3.2.5 Failure Paths

| Scenario | How to trigger | Result |
|---|---|---|
| Cancel at gateway | Click **Cancel** on the SSLCommerz page | 302 to `/payment/cancel?tran_id=...`; order `cancelled`; no enrollment |
| Failed card | Use the sandbox fail card (35xx series) | 302 to `/payment/fail?tran_id=...`; order `failed`; `payment.failed` notification |
| Re-fail a PAID order | `POST /payments/fail/` with a completed order's `tran_id` | 302; order **stays `paid`** — a forged/late fail callback can never undo a completed payment |
| Replay success callback | Re-POST `/payments/success/` with the same `tran_id`/`val_id` | 302; still exactly one enrollment and one `paid` order (idempotent) |

Full negative-path matrix (tamper resistance, duplicate-payment handling,
reconciliation reaper): `docs/api-testing/postman-payments.md`.

### 3.3 My Courses (Dashboard + Player Header)

The course-player UI composes its full page from three calls: `/my-courses/<slug>/`
(header card + progress), `/learn/<slug>/curriculum/` (sidebar, §4.1), and
`/learn/<thing>/<id>/` (the item the learner clicked, §4.2–4.5).

#### 3.3.1 List My Enrollments (Dashboard)

**GET** `{{base_url}}/courses/my-courses/`

**Expected 200 (paginated):**
```json
{
    "success": true,
    "data": {
        "count": 2,
        "next": null,
        "previous": null,
        "results": [
            {
                "id": 11,
                "course": {
                    "id": 101, "title": "Python Backend Bootcamp", "slug": "python-backend-bootcamp",
                    "description": "...", "thumbnail": null, "price": "79.99",
                    "language": "English", "level": "intermediate", "duration_minutes": 240,
                    "instructors": [], "category": {}, "published_at": "..."
                },
                "enrollment_type": "paid",
                "is_active": true,
                "progress_percent": 35,
                "completed_at": null,
                "last_accessed_at": "2026-05-17T09:14:22Z",
                "created_at": "2026-05-01T08:00:00Z"
            }
        ]
    }
}
```

Ordered by `last_accessed_at` (most recent first), then `created_at`. Only the caller's own active enrollments are returned.

#### 3.3.2 Get My-Course Detail (Player Header)

**GET** `{{base_url}}/courses/my-courses/{{course_slug}}/`

**Headers:** enrolled learner OR the course's own instructor JWT (preview).

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "is_instructor": false,
        "enrollment": {
            "id": 11,
            "enrollment_type": "paid",
            "is_active": true,
            "progress_percent": 35,
            "completed_at": null,
            "last_accessed_at": "2026-05-17T09:14:22Z",
            "created_at": "2026-05-01T08:00:00Z"
        },
        "course": {
            "id": 101,
            "title": "Python Backend Bootcamp",
            "slug": "python-backend-bootcamp",
            "description": "Build production APIs with Django and DRF.",
            "thumbnail": null,
            "price": "79.99",
            "language": "English",
            "level": "intermediate",
            "duration_minutes": 240,
            "status": "published",
            "is_published": true,
            "published_at": "2026-04-22T11:00:00Z",
            "instructors": [],
            "partner_institution": null,
            "category": {},
            "learning_objectives": "",
            "prerequisites": "",
            "audiences": "",
            "total_sections": 12,
            "total_content_items": 47
        }
    }
}
```

Notes:
- No curriculum tree here — fetch `/learn/{{course_slug}}/curriculum/` for the sidebar (§4.1).
- `is_instructor: true` → the course's own instructor previewing; `enrollment` is `null` in that case.
- Each GET updates the learner's `last_accessed_at` on the enrollment row.

#### 3.3.3 My-Courses Detail Error Cases

| Scenario | Status | Message |
|---|---|---|
| Unenrolled learner | 403 | `"You do not have access to this course."` |
| Course slug not found | 404 | (default DRF 404) |
| Unauthenticated | 401 | (default DRF 401) |

---

## 4. Learner Consumption (`/learn/...`)

> All `/learn/...` endpoints require a verified-email JWT. `GET` endpoints accept either an enrolled learner or the course's own instructor (preview). `POST /progress/`, `POST /submit/`, `POST /run/`, and `POST /retry/` are learner-only — instructors get 403.

### 4.1 Curriculum Outline

**GET** `{{base_url}}/courses/learn/{{course_slug}}/curriculum/`

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "course": { "id": 101, "slug": "python-backend-bootcamp", "title": "Python Backend Bootcamp" },
        "sections": [
            {
                "id": 11,
                "title": "Getting Started",
                "position": 1,
                "is_locked": false,
                "unlocks_at": null,
                "items": [
                    {
                        "content_id": 201, "object_id": 301, "item_type": "lecture",
                        "position": 1, "title": "Welcome", "lecture_type": "article",
                        "duration_seconds": null, "is_completed": false
                    },
                    {
                        "content_id": 202, "object_id": 302, "item_type": "lecture",
                        "position": 2, "title": "Intro Video", "lecture_type": "video",
                        "duration_seconds": 600, "is_completed": true
                    },
                    { "content_id": 203, "object_id": 50, "item_type": "quiz", "position": 3, "title": "Intro Quiz" },
                    { "content_id": 204, "object_id": 1, "item_type": "coding", "position": 4, "title": "Reverse a String", "difficulty": "easy" }
                ]
            }
        ]
    }
}
```

Notes:
- `is_completed` appears only for learners; instructors previewing get the same payload without that key.
- Heavy item payloads (HLS URLs, quiz questions, article text, coding configs) are not in this response — fetch them from the per-item endpoints below.

**Error cases:**

| Scenario | Status | Body |
|---|---|---|
| Unenrolled learner | 403 | `"You do not have access to this course."` |
| Course slug not found | 404 | — |
| Unauthenticated | 401 | — |

### 4.2 Lecture Detail + Watch Progress

#### 4.2.1 Get Learner Lecture Detail

**GET** `{{base_url}}/courses/learn/lectures/{{lecture_id}}/`

**Video lecture:**
```json
{
    "success": true,
    "data": {
        "id": 302,
        "section_id": 11,
        "title": "Intro Video",
        "lecture_type": "video",
        "article_content": "",
        "stream_master_playlist": "courses/python-backend-bootcamp/lectures/302/hls/.../master.m3u8",
        "stream_renditions": [ { "label": "720p", "playlist": "courses/.../720p/playlist.m3u8" } ],
        "duration_seconds": 600,
        "progress": { "watched_seconds": 120, "is_completed": false, "last_watched_at": "2026-05-17T09:14:22Z" }
    }
}
```

**Article lecture:**
```json
{
    "success": true,
    "data": {
        "id": 301, "section_id": 11, "title": "Welcome", "lecture_type": "article",
        "article_content": "HTTP methods, status codes, and API design basics.",
        "stream_master_playlist": "", "stream_renditions": [], "duration_seconds": null,
        "progress": { "watched_seconds": 0, "is_completed": true, "last_watched_at": "..." }
    }
}
```

`progress` is `null` for the instructor preview caller. `transcoding_error` is never exposed.

**Error cases:**

| Scenario | Status | Body |
|---|---|---|
| Unenrolled learner | 404 | `"Lecture not found."` (existence not leaked) |
| Lecture not found | 404 | same |
| Section not yet released (`unlocks_at` in future) | 422 | `"This content has not been released yet."` |

#### 4.2.2 Upsert Watch Progress

**POST** `{{base_url}}/courses/learn/lectures/{{lecture_id}}/progress/`

**Headers:** enrolled learner JWT (instructors get 403).

```json
{ "watched_seconds": 120, "is_completed": false }
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Progress saved.",
    "data": {
        "lecture_id": 302, "watched_seconds": 120, "is_completed": false,
        "last_watched_at": "2026-05-17T09:14:22Z"
    }
}
```

Notes:
- Idempotent — repeated POSTs with the same body never create duplicate `WatchProgress` rows.
- `watched_seconds` is server-clamped to the active video's `duration_seconds`. If the clamped cursor lands at duration, the server forces `is_completed: true` regardless of what the client sent.
- Article lectures have no duration; `watched_seconds` is forced to `0` on save.
- When `is_completed` flips, a signal recalculates `enrollment.progress_percent`. Re-fetch `/my-courses/` to see the updated rollup.

**Error cases:**

| Scenario | Status | Body |
|---|---|---|
| Negative `watched_seconds` | 400 | `errors.watched_seconds` |
| Missing `is_completed` | 400 | `errors.is_completed: ["This field is required."]` |
| Unenrolled learner | 404 | (existence not leaked) |
| Instructor calling progress endpoint | 403 | `"Only learners can access this resource."` |
| Section not yet released | 422 | (same timing message as 4.2.1) |

### 4.3 Quiz Detail + Submission

#### 4.3.1 Get Learner Quiz Detail (Attempt UI)

**GET** `{{base_url}}/courses/learn/quizzes/{{quiz_id}}/`

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "id": 50, "section_id": 11, "title": "REST Basics Quiz",
        "description": "Checks understanding of HTTP and endpoints.",
        "question_count": 3,
        "questions": [
            {
                "id": 1, "question_text": "Which HTTP method is idempotent?", "position": 1,
                "answers": [
                    { "id": 5, "answer_text": "POST" },
                    { "id": 6, "answer_text": "PUT" },
                    { "id": 7, "answer_text": "PATCH" }
                ]
            }
        ],
        "latest_attempt": { "attempt_id": 12, "score": 2, "max_score": 3, "submitted_at": "2026-05-17T09:14:22Z" }
    }
}
```

`is_correct` is **never** in this payload. `latest_attempt` is `null` if the caller has never submitted (or is an instructor previewing).

#### 4.3.2 Submit a Quiz Attempt

**POST** `{{base_url}}/courses/learn/quizzes/{{quiz_id}}/submit/`

**Headers:** enrolled learner JWT (instructors get 403).

```json
{
    "answers": [
        { "question_id": 1, "selected_answer_id": 6 },
        { "question_id": 2, "selected_answer_id": 9 },
        { "question_id": 3, "selected_answer_id": null }
    ]
}
```

**Expected 200** — score + per-question verdict. `correct_answer_id`/`correct_answer_text` appear **only when `is_correct=false`**:
```json
{
    "success": true,
    "message": "Quiz submitted.",
    "data": {
        "attempt_id": 13, "score": 1, "max_score": 3, "submitted_at": "2026-05-17T09:32:08Z",
        "questions": [
            { "question_id": 1, "question_text": "Which HTTP method is idempotent?", "selected_answer_id": 6, "selected_answer_text": "PUT", "is_correct": true },
            { "question_id": 2, "question_text": "Which status code means \"Created\"?", "selected_answer_id": 9, "selected_answer_text": "204", "is_correct": false, "correct_answer_id": 11, "correct_answer_text": "201" },
            { "question_id": 3, "question_text": "Which header carries the bearer token?", "selected_answer_id": null, "selected_answer_text": null, "is_correct": false, "correct_answer_id": 14, "correct_answer_text": "Authorization" }
        ]
    }
}
```

Notes:
- Each POST creates a **new** `QuizAttempt` row — repeated submits don't overwrite past attempts.
- Unanswered questions score as wrong and reveal the correct answer.
- Each successful submit recalculates `enrollment.progress_percent` (a quiz counts as complete once the learner has ≥1 attempt for it).

**Error cases:**

| Scenario | Status | Body |
|---|---|---|
| `question_id` not in this quiz | 400 | `errors.answers` |
| `selected_answer_id` not under cited question | 400 | `errors.answers` |
| Unenrolled learner | 404 | (existence not leaked) |
| Instructor calling submit | 403 | — |
| Section not yet released | 422 | (same timing message as 4.2.1) |

### 4.4 Assignment Detail + Submission + Polling + Retry

#### 4.4.1 Get Learner Assignment Detail

**GET** `{{base_url}}/courses/learn/assignments/{{assignment_id}}/`

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "id": 1, "section_id": 1, "title": "REST Reflection",
        "description": "Reflect on what you learned.",
        "instructions": "Answer both questions fully.",
        "passing_score": 5, "max_score": 10, "question_count": 2,
        "questions": [
            { "id": 1, "question_text": "What surprised you most about REST design?", "points": 6, "hint": "Reference at least one HTTP verb.", "position": 1 },
            { "id": 2, "question_text": "How does idempotency change retry logic?", "points": 4, "hint": "", "position": 2 }
        ],
        "latest_submission": null
    }
}
```

`model_answer` and `rubric` are **never** present. `latest_submission` summarizes the caller's most recent submission.

#### 4.4.2 Submit an Assignment (Auto-Graded)

**POST** `{{base_url}}/courses/learn/assignments/{{assignment_id}}/submit/`

```json
{
    "answers": [
        { "question_id": 1, "answer_text": "Idempotency means PUT and DELETE are safe to retry without compounding side effects, unlike POST. That guides retry policy at the gateway." },
        { "question_id": 2, "answer_text": "Idempotent verbs let the client retry on network failure without worrying about duplicate state changes." }
    ]
}
```

**Expected 202 Accepted:**
```json
{
    "success": true,
    "message": "Assignment submitted. Grading is in progress.",
    "data": { "submission_id": 7, "assignment_id": 1, "status": "submitted", "submitted_at": "2026-05-20T11:42:18.301Z", "max_score": 10 }
}
```

All questions on the assignment must appear in `answers` (use `""` for a deliberately-blank answer) — missing one → 400. `max_score` is snapshotted at submit time.

#### 4.4.3 Get Submission Detail (Polling Target)

**GET** `{{base_url}}/courses/learn/assignments/submissions/{{submission_id}}/`

**Headers:** the same learner that submitted (other learners → 404).

**While grading (`status: "grading"`):** `total_score: 0`, `criterion_results: []`, no `model_answer`.

**Once graded (`status` in `passed`/`failed`):**
```json
{
    "success": true,
    "data": {
        "submission_id": 7, "assignment_id": 1, "status": "passed",
        "total_score": 10, "max_score": 10,
        "submitted_at": "2026-05-20T11:42:18.301Z", "graded_at": "2026-05-20T11:42:19.522Z",
        "grading_error": "",
        "answers": [
            {
                "question_id": 1, "question_text": "What surprised you most about REST design?",
                "answer_text": "Idempotency means PUT and DELETE are safe to retry ...",
                "score": 6, "max_score": 6,
                "criterion_results": [
                    { "index": 0, "type": "keyword", "matched": true, "points_awarded": 4, "feedback": "Correctly identifies idempotency." },
                    { "index": 1, "type": "any_of", "matched": true, "points_awarded": 2, "feedback": "Mentions an HTTP verb." }
                ],
                "feedback": "Correctly identifies idempotency.\nMentions an HTTP verb.",
                "model_answer": "Reference reflection: idempotency boundaries, ..."
            }
        ]
    }
}
```

`model_answer` is **omitted entirely** unless `status in (passed, failed)`.

**Polling pattern:** poll every 2–5 s after the 202; stop once `status` is `passed`/`failed`/`grading_failed`. If `grading_failed`, show `grading_error` and offer retry (4.4.4).

#### 4.4.4 Retry a Failed Grading

**POST** `{{base_url}}/courses/learn/assignments/submissions/{{submission_id}}/retry/`

**Body:** *(empty)*

**Expected 202** (only when prior status was `grading_failed`):
```json
{ "success": true, "message": "Grading re-enqueued.", "data": { "submission_id": 7, "status": "grading" } }
```

Same row is reused (`submitted_at` unchanged). Any other status → 422 — `"Only submissions in grading_failed can be retried."`. For a fresh attempt after `passed`/`failed`, use `POST /submit/` to create a new row instead.

#### 4.4.5 Error Cases

| Scenario | Status | Body |
|---|---|---|
| In-flight submission already exists (`submitted`/`grading`) | 422 | `"You already have a submission for this assignment that is still being graded."` |
| `question_id` not in assignment / missing a question | 400 | `errors.answers` |
| Unenrolled learner | 404 | (existence not leaked) |
| Instructor calling `/submit/` or `/retry/` | 403 | preview must not pollute submission history |
| Submission detail for another learner's submission | 404 | — |
| Section not yet released | 422 | (same timing message as 4.2.1) |

### 4.5 Coding Exercise Detail + Run + Submit + Polling + Retry

Two execution paths with different persistence semantics:

| Mode | Endpoint | Persisted? | Tests run | Returns | Poll via |
|---|---|---|---|---|---|
| **Run** | `POST /learn/coding-exercises/<id>/run/` | No — Celery result only (expires 1 h) | Visible only | `{task_id}` (202) | `GET /learn/coding-exercises/tasks/<task_id>/` |
| **Submit** | `POST /learn/coding-exercises/<id>/submit/` | Yes — `CodingSubmission` row | All (visible + hidden) | Queued submission (202) | `GET /learn/coding-exercises/submissions/<id>/` |

Execution runs inside a Docker sandbox (network disabled, 128 MB RAM, 0.5 CPU, read-only FS, caps dropped).

#### 4.5.1 Get Learner Coding Exercise Detail

**GET** `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/`

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "id": 1, "section_id": 11, "title": "Sum Two",
        "description": "Sum the two integers in input.",
        "problem_statement": "Given two ints on one line, print their sum.",
        "difficulty": "easy", "default_language": "python",
        "supported_languages": ["python", "javascript"], "time_limit_ms": 2000,
        "language_configs": [ { "id": 1, "language": "python", "starter_code": "def solve(s):\n    pass\n" } ],
        "test_cases": [
            { "id": 1, "input_data": "1 2", "expected_output": "3", "explanation": "easy", "position": 1 },
            { "id": 2, "input_data": "4 5", "expected_output": "9", "explanation": "", "position": 2 }
        ],
        "latest_submission": null
    }
}
```

`solution_code` and hidden test cases are never present on this endpoint.

#### 4.5.2 Run Code (Transient, Visible Tests Only)

**POST** `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/run/`

**Headers:** enrolled learner JWT (instructors get 403).

```json
{ "language": "python", "code": "def solve(s):\n    a, b = map(int, s.split())\n    return a + b\n" }
```

**Expected 202:**
```json
{ "success": true, "message": "Run dispatched.", "data": { "task_id": "e2aa9e54-4a62-48d7-a86b-d599573a462d" } }
```

Does **not** create a `CodingSubmission` row and does **not** affect progress.

#### 4.5.3 Poll a Run Task

**GET** `{{base_url}}/courses/learn/coding-exercises/tasks/{{task_id}}/`

```json
{
    "success": true,
    "data": {
        "state": "SUCCESS",
        "result": {
            "exercise_id": 1, "language": "python", "status": "passed",
            "total_tests": 2, "passed_tests": 2, "score": 100.0, "runtime_ms": 4, "error_message": "",
            "test_results": [
                { "position": 1, "input_data": "1 2", "expected_output": "3", "actual_output": "3", "stdout": "3", "stderr": "", "status": "passed", "runtime_ms": 2, "exit_code": 0 },
                { "position": 2, "input_data": "4 5", "expected_output": "9", "actual_output": "9", "stdout": "9", "stderr": "", "status": "passed", "runtime_ms": 2, "exit_code": 0 }
            ]
        }
    }
}
```

**Polling pattern:** poll every 500 ms after the 202; stop on `state` = `SUCCESS`/`FAILURE`; cap the window around 60 s.

#### 4.5.4 Submit Code (Persisted, All Tests)

**POST** `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/submit/`

```json
{ "language": "python", "code": "def solve(s):\n    a, b = map(int, s.split())\n    return a + b\n" }
```

**Expected 202:**
```json
{
    "success": true,
    "message": "Submission queued.",
    "data": {
        "id": 7, "exercise_id": 1, "language": "python",
        "code": "def solve(s):\n    a, b = map(int, s.split())\n    return a + b\n",
        "status": "queued", "total_tests": 3, "passed_tests": 0, "score": "0.00",
        "runtime_ms": 0, "error_message": "", "stdout": "", "stderr": "",
        "submitted_at": "2026-05-23T11:42:18.301Z", "completed_at": null, "test_results": []
    }
}
```

`total_tests` is snapshotted at submit time; the row is created before the task dispatches, so the frontend always has an `id` to poll.

#### 4.5.5 Get Submission Detail (Polling Target)

**GET** `{{base_url}}/courses/learn/coding-exercises/submissions/{{submission_id}}/`

**Once terminal (`passed`/`failed`/`error`):**
```json
{
    "success": true,
    "data": {
        "id": 7, "exercise_id": 1, "language": "python", "status": "passed",
        "total_tests": 3, "passed_tests": 3, "score": "100.00", "runtime_ms": 15,
        "error_message": "", "stdout": "3\n9\n300", "stderr": "",
        "submitted_at": "2026-05-23T11:42:18.301Z", "completed_at": "2026-05-23T11:42:19.522Z",
        "test_results": [
            { "id": 1, "position": 1, "status": "passed", "runtime_ms": 5, "exit_code": 0, "is_hidden": false, "input_data": "1 2", "expected_output": "3", "actual_output": "3", "stdout": "3", "stderr": "" },
            { "id": 2, "position": 2, "status": "passed", "runtime_ms": 5, "exit_code": 0, "is_hidden": false, "input_data": "4 5", "expected_output": "9", "actual_output": "9", "stdout": "9", "stderr": "" }
        ]
    }
}
```

Hidden test rows are **omitted entirely** from `test_results` (aggregate fields still count them). Status precedence: `error` > `failed` > `passed`. A `passed` terminal status schedules `recalculate_progress`.

**Polling pattern:** poll every 500–1000 ms; stop on `passed`/`failed`/`error`. If `error`, offer retry (4.5.6).

#### 4.5.6 Retry an Errored Submission

**POST** `{{base_url}}/courses/learn/coding-exercises/submissions/{{submission_id}}/retry/`

**Body:** *(empty)*

**Expected 202** (only when prior status was `error`):
```json
{ "success": true, "message": "Submission re-enqueued.", "data": { "submission_id": 7, "status": "queued" } }
```

Only `error` is retryable — `passed`/`failed` → 422 `"Only submissions in error state can be retried."` For a fresh attempt, use `/submit/` to create a new row.

#### 4.5.7 Error Cases

| Scenario | Status | Body |
|---|---|---|
| In-flight submission already exists (`queued`/`grading`) | 422 | `"You already have a submission for this exercise that is still being graded."` |
| Empty `code` | 400 | `"Code cannot be empty."` |
| Language not in exercise's `supported_languages` | 400 | `"Language '<lang>' is not configured for this exercise."` |
| Unenrolled learner | 404 | (existence not leaked) |
| Instructor calling `/run/`, `/submit/`, or `/retry/` | 403 | preview must not pollute submission history |
| Submission detail for another learner's submission | 404 | — |
| Retry on non-`error` submission | 422 | `"Only submissions in error state can be retried."` |
| Section not yet released | 422 | (same timing message as 4.2.1) |

---

## 5. End-to-End Quick Flow

1. **Browse catalog** (2.1.1) — optionally filter by category/price/level.
2. **View course detail** (2.1.3) on a paid course → note `price`, `slug`.
3. Try **direct enroll** (3.2) → confirm the 422 payment gate.
4. **Checkout** (3.2.1) → **pay** on the sandbox gateway (3.2.2) → **verify** (3.2.3): order `paid`, enrollment `paid`.
5. **List my courses** (3.3.1) — confirm the new enrollment appears.
6. **Get my-course detail** (3.3.2) for the player header.
7. **Fetch curriculum** (4.1) — walk the sidebar tree.
8. **Open first lecture** (4.2.1), **post progress** (4.2.2) until `is_completed: true`.
9. **Open the quiz** (4.3.1), **submit answers** (4.3.2) — check score + wrong-answer reveals.
10. **Open the assignment** (4.4.1), **submit** (4.4.2), **poll** (4.4.3) until graded.
11. **Open the coding exercise** (4.5.1), **run** against visible tests (4.5.2–4.5.3), then **submit** for real (4.5.4–4.5.5).
12. **Re-check my-courses** (3.3.1) — `progress_percent` should now reflect every completed item; at 100% a certificate is auto-issued (see `POSTMAN_TESTING_GUIDE.md` §43).
