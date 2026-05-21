# Enrollment API Testing Guide (Postman)

## 1) Base URLs
- Courses base: `http://127.0.0.1:8000/api/v1/courses`
- Auth base: `http://127.0.0.1:8000/api/v1/auth`

## 2) Prerequisites

1. Server running: `python manage.py runserver`
2. Database migrated: `python manage.py migrate`
3. At least one **published** course exists (see Section 3 for demo setup).

## 3) Postman Environment Variables

Create a Postman environment with these variables:

```text
base_url        = http://127.0.0.1:8000/api/v1/courses
auth_base_url   = http://127.0.0.1:8000/api/v1/auth
learner_token   = <fill after learner login>
course_slug     = python-backend-bootcamp
```

## 4) Required Headers

For authenticated endpoints add:
```http
Authorization: Bearer {{learner_token}}
Content-Type: application/json
```

Public catalog endpoints need **no** Authorization header.

---

## 5) Demo Data Setup

Before running enrollment tests you need a learner account and a published course.

### 5.1 Register a Learner Account

- Method: `POST`
- URL: `{{auth_base_url}}/register/`
- Body:
```json
{
  "email": "learner@example.com",
  "password": "TestPass123!",
  "full_name": "Demo Learner",
  "user_type": "learner"
}
```
- Expected status: `201`

Then verify the learner's email via OTP (check the console for the OTP if using the console email backend):

- Method: `POST`
- URL: `{{auth_base_url}}/verify-otp/`
- Body:
```json
{
  "email": "learner@example.com",
  "otp": "123456"
}
```
- Expected status: `200`

### 5.2 Log In as Learner and Copy the Token

- Method: `POST`
- URL: `{{auth_base_url}}/login/`
- Body:
```json
{
  "email": "learner@example.com",
  "password": "TestPass123!"
}
```
- Expected status: `200`
- Copy `data.access` and save it as `learner_token` in your Postman environment.

### 5.3 Ensure a Published Course Exists

Use an admin or instructor account to create and publish a course (see `COURSES_API_TESTING_GUIDE.md`). The slug used in all examples below is `python-backend-bootcamp`. Replace it with your actual slug.

---

## 6) Public Catalog (No Auth Required)

### 6.1 Browse the Catalog

- Method: `GET`
- URL: `{{base_url}}/catalog/`
- No Authorization header needed.
- Expected status: `200`
- Expected response:
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

### 6.2 Filter the Catalog

The catalog supports multi-criteria filtering and sorting. All params are
optional and combine with AND. Successful filtered responses follow the
same `200` paginated shape as 6.1.

#### Quick reference — all supported params

**Filter fields** (all optional, AND-combined):

| Param | Type / accepted values | Example |
|---|---|---|
| `category` | slug (single) | `?category=programming` |
| `subcategory` | slug (single) | `?subcategory=python` |
| `level` | CSV of `beginner`, `intermediate`, `advanced` | `?level=beginner,intermediate` |
| `language` | CSV of language strings (case-insensitive) | `?language=english,bangla` |
| `price_type` | `free` or `paid` | `?price_type=free` |
| `price_min` | non-negative decimal | `?price_min=10` |
| `price_max` | non-negative decimal | `?price_max=99.99` |
| `duration_min` | non-negative integer (minutes) | `?duration_min=60` |
| `duration_max` | non-negative integer (minutes) | `?duration_max=240` |
| `search` | text — matches title, description, instructor full name | `?search=python` |
| `page` | page number (see Section 11) | `?page=2` |
| `page_size` | items per page, max 100 (see Section 11) | `?page_size=25` |

**Sort field** (`?sort=<key>` — one value at a time):

| Key | Effect |
|---|---|
| `relevance` | Title match rank desc, then newest. Default when `?search=` is present. |
| `newest` | `-published_at`. Default when no `?search=`. |
| `popularity` | Active enrollment count desc, then newest. |
| `price_asc` | Price ascending. |
| `price_desc` | Price descending. |
| `rating` | Stubbed — currently falls back to `newest` until the course-rating field ships. |

Per-field detail (validation rules, multi-select behavior, edge cases)
follows in 6.2.1 – 6.2.9.

#### 6.2.1 Category / subcategory

| Filter | Param | Example | Behavior |
|---|---|---|---|
| By top-level category | `?category=<slug>` | `?category=programming` | Matches that category **and** every subcategory under it. |
| By subcategory only | `?subcategory=<slug>` | `?subcategory=python` | Matches the exact subcategory. |
| By both (with validation) | `?category=<parent>&subcategory=<child>` | `?category=programming&subcategory=python` | Only returns rows whose category is the child **and** whose parent is the given category. Mismatched pairs (e.g. `?category=cooking&subcategory=python`) return an empty list. |

#### 6.2.2 Level (multi-select)

CSV — picks multiple levels at once. Accepted values: `beginner`, `intermediate`, `advanced`.

| Example | Result |
|---|---|
| `?level=intermediate` | Intermediate only |
| `?level=beginner,intermediate` | Beginner OR Intermediate |

#### 6.2.3 Language (multi-select, case-insensitive)

CSV — accepts any language string the model stores (case-insensitive match).

| Example | Result |
|---|---|
| `?language=English` | English courses |
| `?language=english,bangla` | English OR Bangla |

#### 6.2.4 Price

| Filter | Param | Example |
|---|---|---|
| Free only | `?price_type=free` | `{{base_url}}/catalog/?price_type=free` |
| Paid only | `?price_type=paid` | `{{base_url}}/catalog/?price_type=paid` |
| Minimum price | `?price_min=<decimal>` | `?price_min=10` |
| Maximum price | `?price_max=<decimal>` | `?price_max=99.99` |
| Price range | both bounds | `?price_min=10&price_max=99.99` |

`price_min` and `price_max` must be non-negative. Negative values return `400`.

#### 6.2.5 Duration (in minutes)

| Filter | Param | Example | Notes |
|---|---|---|---|
| Minimum duration | `?duration_min=<int>` | `?duration_min=60` | 1+ hour |
| Maximum duration | `?duration_max=<int>` | `?duration_max=240` | ≤ 4 hours |
| Duration range | both bounds | `?duration_min=60&duration_max=240` | 1–4 hours |

Frontends rendering "Hours / Weeks / Months" sliders convert to minutes
before sending. Values must be non-negative integers.

#### 6.2.6 Search

`?search=<text>` matches against **title**, **description**, and
**instructor full name** (case-insensitive substring).

| Example | What matches |
|---|---|
| `?search=python` | Any course with "python" in the title or description, or any course taught by an instructor whose name contains "python". |
| `?search=sarah%20chen` | Courses by instructors named "Sarah Chen". |

#### 6.2.7 Sort

`?sort=<key>` — one of:

| Key | Order |
|---|---|
| `relevance` | Title match rank desc, then newest. Default when `?search=` is present. |
| `newest` | `-published_at`. Default when no search term. |
| `popularity` | Active enrollment count desc, then newest. |
| `price_asc` | Price ascending. |
| `price_desc` | Price descending. |
| `rating` | **Stubbed** — currently falls back to `newest` until the course-rating field ships. |

Unknown sort keys return `400` (see 6.2.9).

#### 6.2.8 Combining filters

All params AND together. Example: beginner courses in the `programming-python`
subcategory, under $50, taught in English, ordered by popularity:

```
{{base_url}}/catalog/?category=programming&subcategory=python&level=beginner&price_max=50&language=english&sort=popularity
```

#### 6.2.9 Filter validation errors

Invalid filter values return `400` with a field-keyed `errors` object so
the frontend can highlight every bad input at once.

| Bad input | Status | Sample error |
|---|---|---|
| `?sort=cheapest` | 400 | `{"sort": ["Invalid sort \"cheapest\". Must be one of: newest, popularity, price_asc, price_desc, rating, relevance."]}` |
| `?level=expert` | 400 | `{"level": ["Invalid level(s): expert. Must be one of: advanced, beginner, intermediate."]}` |
| `?level=beginner,foobar` | 400 | `{"level": ["Invalid level(s): foobar. Must be one of: advanced, beginner, intermediate."]}` |
| `?price_min=-10` | 400 | `{"price_min": ["Must be non-negative."]}` |
| `?price_max=abc` | 400 | `{"price_max": ["\"abc\" is not a valid number."]}` |
| `?duration_max=3.5` | 400 | `{"duration_max": ["\"3.5\" is not a valid integer."]}` |
| `?duration_min=-5` | 400 | `{"duration_min": ["Must be non-negative."]}` |

Multiple bad fields are reported in one response:

```http
GET {{base_url}}/catalog/?sort=foo&level=wizard&price_min=-1
```
```json
{
  "success": false,
  "message": "Invalid filter parameters.",
  "errors": {
    "sort": ["Invalid sort \"foo\". Must be one of: newest, popularity, price_asc, price_desc, rating, relevance."],
    "level": ["Invalid level(s): wizard. Must be one of: advanced, beginner, intermediate."],
    "price_min": ["Must be non-negative."]
  }
}
```

### 6.3 View a Single Course Detail

- Method: `GET`
- URL: `{{base_url}}/catalog/{{course_slug}}/`
- No Authorization header needed.
- Expected status: `200`
- Expected response:
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
    "instructors": [
      { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" }
    ],
    "partner_institutions": [],
    "category": { "id": 1, "name": "Backend Development", "slug": "backend" },
    "learning_objectives": [
      { "id": 1, "text": "Build REST APIs with Django REST Framework." }
    ],
    "prerequisites": [
      { "id": 1, "text": "Basic Python knowledge." }
    ],
    "audiences": [
      { "id": 1, "text": "Developers who want to build backend APIs." }
    ],
    "total_sections": 5,
    "total_content_items": 20,
    "published_at": "2026-05-10T09:00:00Z"
  }
}
```

**Error — course not found or not published:**
- Status: `404`
```json
{ "detail": "No NidusCourse matches the given query." }
```

---

## 7) Learner Enrollment

> All endpoints from here require `Authorization: Bearer {{learner_token}}` and the account must be a **learner** with a **verified email**.

### 7.1 Enroll in a Course

- Method: `POST`
- URL: `{{base_url}}/{{course_slug}}/enroll/`
- Body: _(empty — no body needed)_
- Expected status: `201`
- Expected response:
```json
{
  "success": true,
  "message": "Enrolled successfully.",
  "data": {
    "id": 10,
    "course": {
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

> **Note:** Paid courses also enroll as `enrollment_type: "free"` until the payment integration is added.

### 7.2 Unenroll from a Course

- Method: `POST`
- URL: `{{base_url}}/{{course_slug}}/unenroll/`
- Body: _(empty)_
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "message": "Unenrolled successfully. Your progress has been preserved.",
  "data": {
    "id": 10,
    "course": { "...": "same course object as above" },
    "enrollment_type": "free",
    "is_active": false,
    "progress_percent": 0,
    "completed_at": null,
    "last_accessed_at": null,
    "created_at": "2026-05-13T10:30:00Z"
  }
}
```

Notice `is_active` flipped to `false`. Progress is preserved — if the learner re-enrolls, their row is reactivated (not duplicated).

### 7.3 Re-enroll After Unenrolling

- Method: `POST`
- URL: `{{base_url}}/{{course_slug}}/enroll/`
- Body: _(empty)_
- Expected status: `201`
- Same response shape as 7.1, with `is_active: true` again.
- The `id` of the enrollment record stays the same — no duplicate row is created.

---

## 8) My Courses Dashboard

### 8.1 List My Active Enrollments

- Method: `GET`
- URL: `{{base_url}}/my-courses/`
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "data": {
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
      {
        "id": 10,
        "course": {
          "id": 1,
          "title": "Python Backend Bootcamp",
          "slug": "python-backend-bootcamp",
          "price": "79.99",
          "level": "intermediate",
          "duration_minutes": 240,
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
    ]
  }
}
```

Only **active** enrollments are shown. Unenrolled courses do not appear here.

### 8.2 My Course Detail (Updates Last Accessed)

- Method: `GET`
- URL: `{{base_url}}/my-courses/{{course_slug}}/`
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "data": {
    "id": 10,
    "course": {
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
      "partner_institutions": [],
      "category": { "id": 1, "name": "Backend Development", "slug": "backend" },
      "learning_objectives": [ { "id": 1, "text": "Build REST APIs with Django REST Framework." } ],
      "prerequisites": [ { "id": 1, "text": "Basic Python knowledge." } ],
      "audiences": [ { "id": 1, "text": "Developers who want to build backend APIs." } ],
      "total_sections": 5,
      "total_content_items": 20,
      "published_at": "2026-05-10T09:00:00Z"
    },
    "enrollment_type": "free",
    "is_active": true,
    "progress_percent": 0,
    "completed_at": null,
    "last_accessed_at": "2026-05-13T10:45:00Z",
    "created_at": "2026-05-13T10:30:00Z"
  }
}
```

Each time this endpoint is called, `last_accessed_at` is updated automatically in the database.

---

## 9) Error Cases

### 9.1 Enroll Twice (Duplicate)

- Enroll in the same course a second time while already enrolled.
- Expected status: `422`
```json
{
  "success": false,
  "message": "You are already enrolled in this course."
}
```

### 9.2 Unenroll When Not Enrolled

- Call unenroll without ever enrolling.
- Expected status: `422`
```json
{
  "success": false,
  "message": "You are not currently enrolled in this course."
}
```

### 9.3 Non-Learner Tries to Enroll (Instructor Account)

- Log in as an instructor, attempt `POST /{{course_slug}}/enroll/`.
- Expected status: `403`
```json
{
  "detail": "Only learners can access this resource."
}
```

### 9.4 Unverified Learner Tries to Enroll

- Log in as a learner whose email has not been verified.
- Expected status: `403`
```json
{
  "detail": "Email address is not verified."
}
```

### 9.5 Enroll in a Non-Published Course

- Use the slug of a draft or rejected course.
- Expected status: `404`
```json
{ "detail": "No NidusCourse matches the given query." }
```

### 9.6 Unauthenticated Access to Protected Endpoint

- Call `GET /my-courses/` without any Authorization header.
- Expected status: `401`
```json
{
  "detail": "Authentication credentials were not provided."
}
```

### 9.7 My Course Detail — Not Enrolled or Inactive

- Call `GET /my-courses/some-slug/` for a course you are not actively enrolled in.
- Expected status: `404`
```json
{ "detail": "No Enrollment matches the given query." }
```

---

## 10) Quick Test Flow (Copy-Paste Order)

Run these requests in order for a complete end-to-end check:

| Step | Method | URL | Auth | What to verify |
|------|--------|-----|------|----------------|
| 1 | GET | `{{base_url}}/catalog/` | None | `200`, list of published courses |
| 2 | GET | `{{base_url}}/catalog/{{course_slug}}/` | None | `200`, full course detail |
| 3 | GET | `{{base_url}}/catalog/?search=python` | None | `200`, filtered results matching title / description / instructor name |
| 4 | GET | `{{base_url}}/catalog/?level=beginner,intermediate` | None | `200`, only beginner OR intermediate courses |
| 5 | GET | `{{base_url}}/catalog/?price_type=free&sort=newest` | None | `200`, only free courses, newest first |
| 6 | GET | `{{base_url}}/catalog/?price_min=10&price_max=99&duration_min=60&sort=price_asc` | None | `200`, range filters AND'd, ordered by price asc |
| 7 | GET | `{{base_url}}/catalog/?sort=cheapest` | None | `400`, `errors.sort` lists the valid sort keys |
| 8 | GET | `{{base_url}}/catalog/?level=wizard&price_min=-1` | None | `400`, both `errors.level` and `errors.price_min` populated in one response |
| 9 | POST | `{{base_url}}/{{course_slug}}/enroll/` | Learner | `201`, `is_active: true`, `enrollment_type: "free"` |
| 10 | POST | `{{base_url}}/{{course_slug}}/enroll/` | Learner | `422`, duplicate error |
| 11 | GET | `{{base_url}}/my-courses/` | Learner | `200`, enrolled course appears |
| 12 | GET | `{{base_url}}/my-courses/{{course_slug}}/` | Learner | `200`, `last_accessed_at` is now set |
| 13 | POST | `{{base_url}}/{{course_slug}}/unenroll/` | Learner | `200`, `is_active: false` |
| 14 | GET | `{{base_url}}/my-courses/` | Learner | `200`, list is now empty |
| 15 | POST | `{{base_url}}/{{course_slug}}/enroll/` | Learner | `201`, same enrollment `id`, reactivated |

---

## 11) Pagination

All list endpoints (`/catalog/`, `/my-courses/`) use page-based pagination.

| Param | Default | Max | Example |
|-------|---------|-----|---------|
| `page` | 1 | — | `?page=2` |
| `page_size` | 10 | 100 | `?page_size=25` |

Example: `{{base_url}}/catalog/?page=2&page_size=5`
