# Postman Guide — Instructor Course Creation

Instructor-facing walkthrough for creating a course end to end: pick a **category**, create the
course (with the new **text-field metadata**), add **sections** and **content**, then submit for
review.

Applies to both **verified instructors** and **verified partner institutions** — both use the same
authoring endpoints (`IsVerifiedCourseCreator`). Where behavior differs, it's called out.

## Table of Contents

1. [Base URL & Auth](#1-base-url--auth)
2. [Environment Variables](#2-environment-variables)
3. [Step 1 — Pick a Category](#3-step-1--pick-a-category)
4. [Step 2 — Create the Course](#4-step-2--create-the-course)
5. [Step 3 — Read / Update the Course](#5-step-3--read--update-the-course)
6. [Step 4 — Add Sections](#6-step-4--add-sections)
7. [Step 5 — Add Content](#7-step-5--add-content)
8. [Step 6 — Submit for Review](#8-step-6--submit-for-review)
9. [Course Field Reference](#9-course-field-reference)
10. [What Changed](#10-what-changed)

---

## 1. Base URL & Auth

```
http://127.0.0.1:8000/api/v1
```

Every authoring request needs a **verified** creator JWT:

```http
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

- **Instructor**: must have an approved `IdentityVerification` (`InstructorProfile.is_verified = True`).
- **Partner institution**: must have `is_verified = True` and `is_active = True`.

A non-verified caller → `403`. Non-owners of a course → `404` (existence is never leaked).

> File uploads (course `thumbnail`, video lectures): switch Body to **form-data** and omit the
> `Content-Type` header — Postman sets the multipart boundary automatically.

---

## 2. Environment Variables

```text
base_url      = http://127.0.0.1:8000/api/v1
access_token  = <fill after login as a verified instructor / partner institution>
category_id   =
course_id     =
section_id    =
lecture_id    =
quiz_id       =
```

---

## 3. Step 1 — Pick a Category

`category` is **required** when creating a course, so fetch the list first and grab an id.

**GET** `{{base_url}}/courses/categories/` — **public**, no auth needed.

Returns active categories as a 2-level tree (top-level + nested `children`), sorted alphabetically,
paginated (page size 10, `?page=` / `?page_size=`).

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
                "name": "Programming",
                "slug": "programming",
                "children": [
                    { "id": 5, "name": "Python", "slug": "python", "children": [] }
                ]
            },
            {
                "id": 2,
                "name": "Design",
                "slug": "design",
                "children": []
            }
        ]
    }
}
```

Pick either a top-level id or a child id and save it as `{{category_id}}`. Only **active**
categories are assignable to a course.

> **Managing categories is admin-only.** Instructors cannot create/edit categories. If the category
> you need doesn't exist, ask an admin to add it (admin endpoints: `POST/PATCH/DELETE
> {{base_url}}/courses/categories/[<id>/]`, gated `IsPlatformAdmin`). See the main
> `POSTMAN_TESTING_GUIDE.md` §35A for the admin flow.

---

## 4. Step 2 — Create the Course

**POST** `{{base_url}}/courses/create/`

```json
{
    "title": "Python Backend Bootcamp",
    "description": "Build production APIs with Django and DRF.",
    "category": 1,
    "price": "79.99",
    "language": "English",
    "level": "intermediate",
    "duration_minutes": 240,
    "learning_objectives": "Build production REST APIs.\nContainerize with Docker.\nWrite tests.",
    "prerequisites": "Comfortable with Python.\nBasic HTTP knowledge.",
    "audiences": "Backend engineers.\nAspiring DevOps engineers."
}
```

**Required:** `title` (≥ 5 chars), `description`, `category` (active category id).
Everything else is optional.

**`learning_objectives` / `prerequisites` / `audiences`** are plain **text fields** — put **one
item per line** (`\n`-separated). The frontend splits on newline to render bullet lists.

**`level` options:** `beginner`, `intermediate`, `advanced` (default `beginner`).

**Expected 201:** the full course object is returned (save `data.id` as `{{course_id}}`):
```json
{
    "success": true,
    "message": "Course created successfully.",
    "data": {
        "id": 101,
        "title": "Python Backend Bootcamp",
        "slug": "python-backend-bootcamp",
        "description": "Build production APIs with Django and DRF.",
        "thumbnail": null,
        "price": "79.99",
        "language": "English",
        "level": "intermediate",
        "duration_minutes": 240,
        "status": "draft",
        "is_published": false,
        "rejection_reason": "",
        "published_at": null,
        "created_by": { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" },
        "instructors": [ { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" } ],
        "partner_institution": null,
        "category": { "id": 1, "name": "Programming", "slug": "programming" },
        "learning_objectives": "Build production REST APIs.\nContainerize with Docker.\nWrite tests.",
        "prerequisites": "Comfortable with Python.\nBasic HTTP knowledge.",
        "audiences": "Backend engineers.\nAspiring DevOps engineers.",
        "created_at": "...",
        "updated_at": "..."
    }
}
```

New courses start in `status: "draft"`. For a **partner institution** creator, `partner_institution`
is auto-set and `instructors` starts empty (experts are added separately); for an **instructor**
creator, they're auto-added to `instructors`.

### Upload a thumbnail (form-data)

Send the create (or a later PATCH) as **form-data**:

| Key | Type | Value |
|---|---|---|
| `title` | Text | Python Backend Bootcamp |
| `description` | Text | ... |
| `category` | Text | 1 |
| `thumbnail` | File | *(select an image)* |

### Create error cases

| Scenario | Status | Detail |
|---|---|---|
| Missing `category` | 400 | `errors.category: ["This field is required."]` |
| `category` is an inactive / unknown id | 400 | `errors.category: ["Invalid pk ... object does not exist."]` |
| `title` < 5 chars | 400 | `errors.title: ["Title must be at least 5 characters long."]` |
| Missing `description` | 400 | `errors.description: ["This field is required."]` |
| Not a verified creator | 403 | `"..."` (permission denied) |

---

## 5. Step 3 — Read / Update the Course

**GET** `{{base_url}}/courses/{{course_id}}/` — authoring view (owner or assigned instructor). Same
object shape as the create response.

**PATCH** `{{base_url}}/courses/{{course_id}}/` — partial update.

```json
{
    "title": "Python Backend Bootcamp (2026 Edition)",
    "price": "89.99",
    "learning_objectives": "Design REST endpoints.\nSecure APIs with JWT.\nDeploy to production."
}
```

- Supplying any of `learning_objectives` / `prerequisites` / `audiences` **replaces that field's
  whole value** (it's a single string).
- On PATCH, `category` is not required (partial), but if sent it must be an active id.
- `status`, `rejection_reason`, `partner_institution`, `instructors`, `created_by` are **not**
  writable here — use the status-transition endpoints (Step 6) and the roster/invite flows.
- Writes only succeed while the course is **editable** (`draft` or `rejected`). On
  `under_review` / `institution_review` / `published` / `archived` → **422**
  `"Course is not editable in its current status."`

---

## 6. Step 4 — Add Sections

**POST** `{{base_url}}/courses/{{course_id}}/sections/create/`

```json
{ "title": "Getting Started", "description": "Setup and project structure", "position": 1 }
```

**Expected 201:** save `data.id` as `{{section_id}}`.

- **List:** `GET {{base_url}}/courses/{{course_id}}/sections/` (`?ordering=position` / `-position`).
- **Get / update / delete:** `GET|PATCH|PUT|DELETE {{base_url}}/courses/sections/{{section_id}}/`.

---

## 7. Step 5 — Add Content

All content (lectures, quizzes, coding exercises, assignments) is created through **one** endpoint,
distinguished by `item_type`:

**POST** `{{base_url}}/courses/sections/{{section_id}}/contents/`

### Article lecture
```json
{
    "item_type": "lecture",
    "title": "REST Fundamentals",
    "lecture_type": "article",
    "article_content": "HTTP methods, status codes, and API design basics.",
    "position": 1
}
```
Save `data.object_id` as `{{lecture_id}}`.

### Video lecture (form-data)

| Key | Value |
|---|---|
| `item_type` | `lecture` |
| `title` | Intro Video |
| `lecture_type` | `video` |
| `video_file` | *(select file)* |

The video is queued for transcoding — poll `GET {{base_url}}/courses/lectures/{{lecture_id}}/`
until `active_video_asset.status` is `ready`.

### Quiz
```json
{ "item_type": "quiz", "title": "REST Basics Quiz", "description": "Checks HTTP understanding.", "position": 2 }
```
Save `data.object_id` as `{{quiz_id}}`, then add questions/answers via
`{{base_url}}/courses/quizzes/{{quiz_id}}/questions/` and `.../quiz-questions/<id>/answers/`.

### Coding exercise
```json
{
    "item_type": "coding",
    "title": "Reverse a String",
    "problem_statement": "Given a string s, return it reversed.",
    "difficulty": "easy",
    "default_language": "python",
    "supported_languages": ["python", "javascript"],
    "position": 3
}
```

### Assignment
```json
{
    "item_type": "assignment",
    "title": "Reflection Essay",
    "instructions": "Write at least 300 words.",
    "total_score": 100,
    "passing_score": 60,
    "position": 4
}
```

> Full content/question/rubric detail lives in the main `POSTMAN_TESTING_GUIDE.md` §29–33. This guide
> focuses on the create flow; content sub-resources are unchanged.

---

## 8. Step 6 — Submit for Review

Before submitting, the course must be complete: title/description, ≥ 1 section, each section has
content, all videos `ready`, all quizzes have questions with a correct answer.

**Individual instructor** (no partner institution):

**POST** `{{base_url}}/courses/{{course_id}}/submit/` → `draft` → `under_review` (admin reviews).

**Partner-institution course** (two-stage):

1. Expert: **POST** `{{base_url}}/courses/{{course_id}}/finish/` → `draft` → `institution_review`.
2. Institution browses its queue: **GET** `{{base_url}}/courses/institution-review-queue/` — lists
   the caller institution's own `institution_review` courses, paginated. No course id needed; scope
   comes entirely from the authenticated institution.
3. Institution: **POST** `{{base_url}}/courses/{{course_id}}/institution-review/` with
   `{ "action": "submit" }` → `under_review`, or `{ "action": "send_back", "rejection_reason": "..." }`
   → `rejected`.

Other transitions: `/rework/` (`rejected` → `draft`), `/archive/` (`published` → `archived`,
`archived` → `draft`). Admin first browses **GET** `{{base_url}}/courses/admin/pending-review/`
(all `under_review` courses, oldest-first, paginated) to find work, then approves/rejects the chosen
course via `/review/`. Both queue endpoints accept `?delivery_mode=self_paced|scheduled` to narrow
the list; an unrecognized value → `400`.

---

## 9. Course Field Reference

| Field | Type | Writable | Notes |
|---|---|---|---|
| `title` | string | create/patch | Required, ≥ 5 chars |
| `description` | string | create/patch | Required |
| `category` | int (FK) | create/patch | **Required on create**; must be an active category id |
| `price` | decimal string | create/patch | Default `0`; ≥ 0 |
| `language` | string | create/patch | Default `English` |
| `level` | string | create/patch | `beginner` / `intermediate` / `advanced` |
| `duration_minutes` | int | create/patch | Optional |
| `thumbnail` | image | create/patch | form-data only |
| `learning_objectives` | text | create/patch | Newline-separated; one item per line |
| `prerequisites` | text | create/patch | Newline-separated; one item per line |
| `audiences` | text | create/patch | Newline-separated; one item per line |
| `slug` | string | read-only | Auto-generated from title |
| `status` | string | read-only | Change via transition endpoints |
| `is_published` | bool | read-only | |
| `created_by` / `instructors` / `partner_institution` | — | read-only | Set by the server / roster flows |

---

## 10. What Changed

Two recent changes affect course creation:

1. **`category` is now required on create.** Fetch an active category id from
   `GET /courses/categories/` first. (PATCH stays optional — partial updates.)

2. **`learning_objectives`, `prerequisites`, `audiences` are now plain text fields**, not
   sub-resources. Previously each was a separate table with its own
   `learning-objectives/` / `prerequisites/` / `audiences/` CRUD endpoints — **those endpoints are
   removed**. Now:
   - Set them in the course create / PATCH payload as `\n`-separated strings (one item per line).
   - Read them back as strings on any course detail/list response.
   - The `objective_id` / `prerequisite_id` / `audience_id` variables no longer exist.
