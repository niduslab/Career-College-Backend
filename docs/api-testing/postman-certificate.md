# Postman Guide — Certificate System

Manual API testing for the full certificate flow: automatic issuance on course completion, learner fetch, public verification, and PDF download.

---

## Environment Variables

Set these in your Postman environment before running the collection.

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `learner_token` | `Bearer eyJ...` | JWT for an active learner |
| `other_learner_token` | `Bearer eyJ...` | JWT for a second learner (not enrolled in the test course) |
| `instructor_token` | `Bearer eyJ...` | JWT for a verified instructor |
| `course_slug` | `intro-to-django` | Slug of a published course the learner will complete |
| `certificate_uid` | _(filled during tests)_ | UUID of the issued certificate |

---

## Prerequisites

1. A **published** course exists with at least one piece of content.
2. The learner account has `user_type = learner` and `is_email_verified = True`.
3. Celery worker is running (`celery -A career_college_backend worker -l info`) — certificate email is sent asynchronously.
4. `EMAIL_BACKEND = django.core.mail.backends.console.EmailBackend` for local dev so emails print to the terminal instead of sending.

---

## Setup: Trigger Certificate Issuance

A certificate is issued automatically when a learner reaches 100% progress. Complete the following to create the test fixture.

### Step 1 — Enroll in the course

```
POST {{base_url}}/courses/{{course_slug}}/enroll/
Authorization: {{learner_token}}
```

**Expected:** `201 Created`.

```json
{
    "success": true,
    "message": "Enrolled successfully.",
    "data": {
        "course": "intro-to-django",
        "enrollment_type": "free",
        "is_active": true,
        "progress_percent": 0,
        "completed_at": null
    }
}
```

---

### Step 2 — Complete all course content

Complete every piece of content in the course so `progress_percent` reaches 100.

**Lectures** — POST a `watched_seconds` + `is_completed: true` progress update for each lecture:

```
POST {{base_url}}/courses/learn/lectures/{{lecture_id}}/progress/
Authorization: {{learner_token}}
Content-Type: application/json

{
    "watched_seconds": 600,
    "is_completed": true
}
```

**Quizzes** — submit at least one attempt per quiz (any score counts as complete):

```
POST {{base_url}}/courses/learn/quizzes/{{quiz_id}}/submit/
Authorization: {{learner_token}}
Content-Type: application/json

{
    "answers": [
        { "question_id": 1, "answer_id": 3 }
    ]
}
```

**Assignments** — submit and achieve a `passed` status:

```
POST {{base_url}}/courses/learn/assignments/{{assignment_id}}/submit/
Authorization: {{learner_token}}
Content-Type: application/json

{
    "answers": [
        { "question_id": 1, "answer_text": "Your answer here." }
    ]
}
```

Poll until `status == "passed"`:

```
GET {{base_url}}/courses/learn/assignments/submissions/{{submission_id}}/
Authorization: {{learner_token}}
```

> **Shortcut for testing:** If the course has only lectures, mark all of them completed. The certificate fires as soon as `recalculate_progress` computes 100%.

---

### Step 3 — Verify enrollment is complete

```
GET {{base_url}}/courses/my-courses/{{course_slug}}/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`. Confirm `progress_percent == 100` and `completed_at` is a non-null timestamp.

```json
{
    "success": true,
    "data": {
        "title": "Intro to Django",
        "progress_percent": 100,
        "completed_at": "2026-06-10T09:00:00Z",
        ...
    }
}
```

> If `completed_at` is null but all content is marked done, check that the Celery worker is running and that `recalculate_progress` was triggered (check worker logs).

---

### Step 4 — Confirm certificate was issued

```
GET {{base_url}}/courses/my-courses/{{course_slug}}/certificate/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`.

```json
{
    "success": true,
    "message": "Certificate retrieved.",
    "data": {
        "certificate_uid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "learner_name": "Jane Doe",
        "course_title": "Intro to Django",
        "issued_at": "2026-06-10T09:00:00Z"
    }
}
```

**Postman Test — save certificate_uid:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("certificate_uid present", () => {
    const uid = pm.response.json().data.certificate_uid;
    pm.expect(uid).to.be.a("string");
    pm.environment.set("certificate_uid", uid);
});
pm.test("is_valid not in learner response", () => {
    pm.expect(pm.response.json().data).to.not.have.property("is_valid");
});
```

> `is_valid` is intentionally absent from the authenticated learner endpoint. It only appears on the public `/verify/` endpoint.

---

## Group 1: Fetch Learner's Own Certificate

### 1.1 Happy path — enrolled and completed

_(Already covered in Setup Step 4 above.)_

---

### 1.2 Enrolled but course not yet completed — 404

Enroll a fresh learner but do not complete any content. Then:

```
GET {{base_url}}/courses/my-courses/{{course_slug}}/certificate/
Authorization: {{other_learner_token}}
```

**Expected:** `404 Not Found`.

```json
{
    "success": false,
    "message": "Certificate not yet issued. Complete the course first."
}
```

**Postman Test:**
```javascript
pm.test("404 for incomplete course", () => pm.response.to.have.status(404));
pm.test("message mentions completion", () => {
    pm.expect(pm.response.json().message).to.include("Complete the course");
});
```

---

### 1.3 Not enrolled — 403 (slug-based policy)

Use a learner who has never enrolled in the course.

```
GET {{base_url}}/courses/my-courses/{{course_slug}}/certificate/
Authorization: {{other_learner_token}}
```

**Expected:** `403 Forbidden`.

```json
{
    "success": false,
    "message": "You are not enrolled in this course."
}
```

**Postman Test:**
```javascript
pm.test("403 not 404 for unenrolled (slug policy)", () => pm.response.to.have.status(403));
```

> This is intentional. Course slugs are public (appear in `/catalog/`). A 403 confirms the course exists but the caller has no access. A numeric-ID endpoint would return 404 — see the 403 vs 404 policy in [CLAUDE.md](../../CLAUDE.md).

---

### 1.4 Instructor tries to fetch their own certificate — 403

Instructors cannot earn certificates (they create the course, they don't learn it).

```
GET {{base_url}}/courses/my-courses/{{course_slug}}/certificate/
Authorization: {{instructor_token}}
```

**Expected:** `403 Forbidden` (blocked by `IsLearnerUser` permission class before reaching the view logic).

**Postman Test:**
```javascript
pm.test("instructor gets 403", () => pm.response.to.have.status(403));
```

---

### 1.5 No auth — 401

```
GET {{base_url}}/courses/my-courses/{{course_slug}}/certificate/
```

**Expected:** `401 Unauthorized`.

---

### 1.6 Course slug does not exist — 404

```
GET {{base_url}}/courses/my-courses/nonexistent-course-xyz/certificate/
Authorization: {{learner_token}}
```

**Expected:** `404 Not Found`, `message: "Course not found."`.

**Postman Test:**
```javascript
pm.test("404 for missing slug", () => pm.response.to.have.status(404));
pm.test("correct message", () => {
    pm.expect(pm.response.json().message).to.equal("Course not found.");
});
```

---

## Group 2: Public Certificate Verification

> Precondition: `certificate_uid` is set from Setup Step 4 or Group 1.1.

### 2.1 Valid certificate — happy path

No `Authorization` header needed.

```
GET {{base_url}}/courses/certificates/{{certificate_uid}}/verify/
```

**Expected:** `200 OK`.

```json
{
    "success": true,
    "message": "Certificate is valid.",
    "data": {
        "certificate_uid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "learner_name": "Jane Doe",
        "course_title": "Intro to Django",
        "issued_at": "2026-06-10T09:00:00Z",
        "is_valid": true
    }
}
```

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("is_valid is true", () => {
    pm.expect(pm.response.json().data.is_valid).to.equal(true);
});
pm.test("no sensitive fields", () => {
    const d = pm.response.json().data;
    pm.expect(d).to.not.have.property("enrollment_id");
    pm.expect(d).to.not.have.property("id");
});
```

---

### 2.2 Unknown UUID — 404

```
GET {{base_url}}/courses/certificates/00000000-0000-0000-0000-000000000000/verify/
```

**Expected:** `404 Not Found`.

```json
{
    "success": false,
    "message": "Certificate not found."
}
```

**Postman Test:**
```javascript
pm.test("404 for unknown uuid", () => pm.response.to.have.status(404));
pm.test("message does not reveal whether other certs exist", () => {
    pm.expect(pm.response.json().message).to.equal("Certificate not found.");
});
```

---

### 2.3 Malformed UUID — 404

Django's `<uuid:certificate_uid>` URL pattern rejects non-UUID strings at the routing layer.

```
GET {{base_url}}/courses/certificates/not-a-uuid/verify/
```

**Expected:** `404 Not Found` (Django URL resolver returns a `404` before the view runs).

---

### 2.4 Works without auth — confirm public access

```
GET {{base_url}}/courses/certificates/{{certificate_uid}}/verify/
```

No `Authorization` header. **Expected:** `200 OK` (same as 2.1). The endpoint is `AllowAny`.

---

### 2.5 Works with auth — same response

```
GET {{base_url}}/courses/certificates/{{certificate_uid}}/verify/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, identical response to 2.1. Auth headers are ignored — the endpoint does not change behaviour based on caller identity.

---

## Group 3: PDF Download

> Precondition: `certificate_uid` is set.

### 3.1 Valid certificate — PDF bytes returned

```
GET {{base_url}}/courses/certificates/{{certificate_uid}}/download/
```

**Expected:** `200 OK`, `Content-Type: application/pdf`, `Content-Disposition: attachment; filename="certificate-<uuid>.pdf"`.

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("content-type is pdf", () => {
    pm.expect(pm.response.headers.get("Content-Type")).to.include("application/pdf");
});
pm.test("content-disposition is attachment", () => {
    const cd = pm.response.headers.get("Content-Disposition");
    pm.expect(cd).to.include("attachment");
    pm.expect(cd).to.include("certificate-");
    pm.expect(cd).to.include(".pdf");
});
```

> In Postman, click **Save Response → Save to a file** to inspect the downloaded PDF visually.

---

### 3.2 Unknown UUID — 404 JSON

```
GET {{base_url}}/courses/certificates/00000000-0000-0000-0000-000000000000/download/
```

**Expected:** `404 Not Found`, `Content-Type: application/json`.

```json
{
    "success": false,
    "message": "Certificate not found."
}
```

**Postman Test:**
```javascript
pm.test("404 for unknown uuid", () => pm.response.to.have.status(404));
pm.test("json response body", () => {
    pm.expect(pm.response.headers.get("Content-Type")).to.include("json");
});
```

---

### 3.3 No auth needed — public download

```
GET {{base_url}}/courses/certificates/{{certificate_uid}}/download/
```

No `Authorization` header. **Expected:** `200 OK` with PDF bytes. Anyone with the UUID can download.

---

### 3.4 Filename includes certificate UUID

Verify the `Content-Disposition` filename matches the UUID in the URL:

**Postman Test:**
```javascript
const uid = pm.environment.get("certificate_uid");
const cd = pm.response.headers.get("Content-Disposition");
pm.test("filename matches certificate uid", () => {
    pm.expect(cd).to.include(uid);
});
```

---

## Group 4: Data Integrity

These tests verify that certificate data is immutably snapshotted at issue time.

### 4.1 Learner name snapshot — change name, certificate unchanged

1. Note the `learner_name` from the certificate (Group 1.1 or Setup Step 4).
2. Update the learner's `full_name` via the profile endpoint (if available), or via Django admin.
3. Re-fetch the certificate:

```
GET {{base_url}}/courses/my-courses/{{course_slug}}/certificate/
Authorization: {{learner_token}}
```

**Expected:** `learner_name` in the response is still the **original** name at issue time, not the updated name.

---

### 4.2 Certificate is idempotent — duplicate issuance impossible

Manually call `issue_certificate` again (simulate by triggering `recalculate_progress` a second time via a second lecture progress update, or via Django shell):

```python
# Django shell
from courses.models import Enrollment
from courses.services.certificate_service import issue_certificate

enrollment = Enrollment.objects.get(user__email="learner@example.com", course__slug="intro-to-django")
c1 = issue_certificate(enrollment)
c2 = issue_certificate(enrollment)
assert c1.pk == c2.pk
assert c1.certificate_uid == c2.certificate_uid
```

**Expected:** Same `Certificate` row returned both times. The `Certificate` table has a `UNIQUE` constraint on `enrollment_id`, so a second call is a no-op.

---

### 4.3 Verify UUID uniqueness — two learners get different UUIDs

Enroll a second learner, complete the course for them, then compare UUIDs:

```
GET {{base_url}}/courses/my-courses/{{course_slug}}/certificate/
Authorization: {{learner_token}}

GET {{base_url}}/courses/my-courses/{{course_slug}}/certificate/
Authorization: {{other_learner_token}}
```

**Expected:** Both return `200 OK` with different `certificate_uid` values.

---

## Group 5: Email (Console Backend)

With `EMAIL_BACKEND = django.core.mail.backends.console.EmailBackend`, the certificate email prints to the terminal when the Celery task runs.

### 5.1 Email sent on completion

After completing the course (Setup Step 2), check the Celery worker terminal output for:

```
Subject: Congratulations! You completed "Intro to Django"
From: noreply@careercollege.com
To: learner@example.com
```

The email body should contain:
- Learner's name
- Course title
- Issue date
- View Certificate link: `http://localhost:3000/certificates/<uuid>`
- Download PDF link

### 5.2 Email not sent twice

Complete the course once more (e.g. mark a lecture completed → `recalculate_progress` runs again, progress stays 100%). Check that `send_certificate_email_task` is **not** dispatched a second time.

The `_issue_certificate_and_notify` helper only calls `send_certificate_email_task.delay()` when `issue_certificate` **creates** a new row. Since `get_or_create` returns `created=False` on repeat calls, no second email is queued.

> Verify: in the Celery worker logs, confirm only one `send_certificate_email_task` entry appears for this enrollment.

---

## Response Shape Reference

**Learner certificate (200):**
```json
{
    "success": true,
    "message": "Certificate retrieved.",
    "data": {
        "certificate_uid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "learner_name": "Jane Doe",
        "course_title": "Intro to Django",
        "issued_at": "2026-06-10T09:00:00Z"
    }
}
```

**Public verify (200):**
```json
{
    "success": true,
    "message": "Certificate is valid.",
    "data": {
        "certificate_uid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "learner_name": "Jane Doe",
        "course_title": "Intro to Django",
        "issued_at": "2026-06-10T09:00:00Z",
        "is_valid": true
    }
}
```

**PDF download (200):** Raw PDF bytes. `Content-Type: application/pdf`.

**Not found (404):**
```json
{
    "success": false,
    "message": "Certificate not found."
}
```

**Not enrolled (403):**
```json
{
    "success": false,
    "message": "You are not enrolled in this course."
}
```

**Not yet completed (404):**
```json
{
    "success": false,
    "message": "Certificate not yet issued. Complete the course first."
}
```

---

## Error Code Summary

| Scenario | Endpoint | Status |
|----------|----------|--------|
| Not enrolled (slug) | `my-courses/<slug>/certificate/` | **403** |
| Course slug not found | `my-courses/<slug>/certificate/` | 404 |
| Enrolled, not completed | `my-courses/<slug>/certificate/` | 404 |
| Instructor calls learner endpoint | `my-courses/<slug>/certificate/` | 403 |
| No auth | `my-courses/<slug>/certificate/` | 401 |
| UUID not found | `certificates/<uuid>/verify/` | 404 |
| UUID not found | `certificates/<uuid>/download/` | 404 |
| Malformed UUID in path | any `certificates/<uuid>/...` | 404 (URL resolver) |
| PDF generation failure | `certificates/<uuid>/download/` | 500 |

---

## Field Visibility Policy

| Field | Learner `my-courses/.../certificate/` | Public `/verify/` | Public `/download/` |
|-------|--------------------------------------|-------------------|---------------------|
| `certificate_uid` | ✓ | ✓ | (in filename) |
| `learner_name` | ✓ | ✓ | ✓ (on PDF) |
| `course_title` | ✓ | ✓ | ✓ (on PDF) |
| `issued_at` | ✓ | ✓ | ✓ (on PDF) |
| `is_valid` | ✗ | ✓ (always `true`) | — |
| `enrollment_id` | ✗ | ✗ | ✗ |
| DB `id` (PK) | ✗ | ✗ | ✗ |

---

## Recommended Run Order

```
Setup
  1. POST   enroll
  2.        complete all content (lectures / quizzes / assignments)
  3. GET    my-courses detail  → confirm progress_percent=100, completed_at set
  4. GET    my-courses certificate  → save certificate_uid

Group 1: Learner fetch
  1.1  Own certificate         → 200, save uid
  1.2  Enrolled, not done      → 404
  1.3  Not enrolled            → 403
  1.4  Instructor              → 403
  1.5  No auth                 → 401
  1.6  Bad slug                → 404

Group 2: Public verify
  2.1  Valid uuid (no auth)    → 200, is_valid=true
  2.2  Unknown uuid            → 404
  2.3  Malformed uuid          → 404
  2.4  No auth                 → 200
  2.5  With auth               → 200 (same)

Group 3: PDF download
  3.1  Valid uuid              → 200 PDF
  3.2  Unknown uuid            → 404 JSON
  3.3  No auth                 → 200 PDF
  3.4  Filename includes uuid

Group 4: Data integrity
  4.1  Name snapshot unchanged after profile update
  4.2  Idempotent (Django shell)
  4.3  Two learners get different UUIDs

Group 5: Email (check Celery terminal)
  5.1  Email appears on first completion
  5.2  Email not sent twice
```
