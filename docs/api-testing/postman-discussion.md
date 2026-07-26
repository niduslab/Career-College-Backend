# Postman Guide — Course Q&A / Discussion

Manual API testing for the course Q&A / discussion board. Enrolled learners ask
questions and reply; the course's instructors answer, pin, and moderate. Access is
resolved **in the service layer** — only an active enrolled learner, a course
instructor (`course.instructors` or `created_by`), or a platform admin may read or
write. There is no public/guest surface.

Covers: the enrolled-or-instructor access gate, question create/list/detail/delete,
replies (with instructor badge), upvote (counter-only) on questions and replies,
instructor pin, soft delete, list filtering/pagination, and notification integration.

> **Upvotes are counter-only.** `POST .../upvote/` just increments `upvote_count` —
> there is no per-user vote record, so it does **not** dedup, has **no** un-upvote,
> and payloads carry **no** `viewer_upvoted` flag. Deliberate MVP simplification.
> Because of that, the upvote endpoints are rate-limited per user (default
> `30/min`) — see §4.5.

Design reference: `docs/architecture/26-discussion-qa.md`.

**Access-denied convention** (project-wide 403-vs-404 rule):
- Slug entry points (`<slug>/questions/`) → **403** on no access.
- Numeric-ID entry points (`questions/<id>/`, `replies/<id>/`, votes, pin) → **404**
  (IDs are not public-enumerable; a 404 doesn't confirm existence).

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `learner_token` | `Bearer eyJ...` | JWT for a learner **actively enrolled** in the test course |
| `learner2_token` | `Bearer eyJ...` | JWT for a **second** enrolled learner (reply + upvote tests) |
| `stranger_token` | `Bearer eyJ...` | JWT for a learner **not enrolled** — access-denial checks |
| `instructor_token` | `Bearer eyJ...` | JWT for an instructor in `course.instructors.all()` |
| `admin_token` | `Bearer eyJ...` | JWT for a platform admin |
| `course_slug` | `python-fundamentals` | Slug of a **published** course the learner is enrolled in |
| `content_id` | `10` | PK of a `SectionContent` slot in that course (anchor tests) |
| `question_id` | _(filled during tests)_ | PK of a created question |
| `reply_id` | _(filled during tests)_ | PK of a created reply |

---

## Prerequisites

1. Django dev server running.
2. Redis + a Celery worker running (in-app notifications push over Channels; these
   events are `skip_email=True` so no email is sent — worker still needed for the WS
   push path if you want to observe it): `celery -A career_college_backend worker -Q celery,notifications -l info`.
3. Data:
   - A **published** course (`is_published=True`). Note `course_slug`.
   - The course has **≥1 instructor** in `course.instructors.all()`. Note `instructor_token`.
   - **Two** learners with active enrollments (`Enrollment.is_active=True`) in that
     course → `learner_token`, `learner2_token`.
   - A third learner **not** enrolled → `stranger_token`.
   - At least one `SectionContent` slot (a lecture/quiz/assignment/coding item) in
     the course → `content_id`.

All endpoints require `IsAuthenticated` + `IsEmailVerified`, so every token must be
for an email-verified account.

---

## Group 1: Access Gate

### 1.1 Unenrolled learner cannot list questions (slug → 403)

```
GET {{base_url}}/courses/{{course_slug}}/questions/
Authorization: {{stranger_token}}
```

**Expected:** `403 Forbidden`.

```json
{
  "success": false,
  "message": "You must be enrolled in this course to access its discussion."
}
```

```javascript
pm.test("403 for unenrolled", () => pm.response.to.have.status(403));
pm.test("no data leaked", () => pm.expect(pm.response.json().success).to.be.false);
```

### 1.2 Enrolled learner can list questions (empty at first)

```
GET {{base_url}}/courses/{{course_slug}}/questions/
Authorization: {{learner_token}}
```

**Expected:** `200 OK` with the paginated envelope.

```json
{
  "success": true,
  "data": { "count": 0, "next": null, "previous": null, "results": [] }
}
```

### 1.3 Instructor also has access

```
GET {{base_url}}/courses/{{course_slug}}/questions/
Authorization: {{instructor_token}}
```

**Expected:** `200 OK`. Instructors are participants, not blocked by the enrolled-only rule.

---

## Group 2: Questions

### 2.1 Learner posts a question — happy path

```
POST {{base_url}}/courses/{{course_slug}}/questions/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "title": "How does the event loop work?",
  "body": "I don't understand the ordering of callbacks in lecture 3.",
  "related_content_id": {{content_id}}
}
```

> `related_content_id` is **optional**. Omit it for a general course-level question.

**Expected:** `201 Created`.

```json
{
  "success": true,
  "message": "Question posted.",
  "data": {
    "id": 1,
    "title": "How does the event loop work?",
    "body": "I don't understand the ordering of callbacks in lecture 3.",
    "author_name": "Alice Smith",
    "related_content": { "id": 10, "item_type": "lecture" },
    "is_pinned": false,
    "reply_count": 0,
    "upvote_count": 0,
    "is_own": true,
    "created_at": "2026-07-22T10:00:00Z",
    "updated_at": "2026-07-22T10:00:00Z"
  }
}
```

**Postman test — save question_id:**
```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.test("save question id", () => {
    const id = pm.response.json().data.id;
    pm.expect(id).to.be.a("number");
    pm.environment.set("question_id", id);
});
pm.test("anchored to content", () => {
    pm.expect(pm.response.json().data.related_content.id).to.eql(Number(pm.environment.get("content_id")));
});
```

### 2.2 Blank title/body rejected (400)

```
POST {{base_url}}/courses/{{course_slug}}/questions/
Authorization: {{learner_token}}
Content-Type: application/json

{ "title": "   ", "body": "" }
```

**Expected:** `400 Bad Request` with `errors`.

### 2.3 related_content from another course rejected (400)

Use a `SectionContent` id that belongs to a **different** course.

```
POST {{base_url}}/courses/{{course_slug}}/questions/
Authorization: {{learner_token}}
Content-Type: application/json

{ "title": "Q", "body": "B", "related_content_id": 99999 }
```

**Expected:** `400 Bad Request`.

```json
{ "success": false, "message": "The referenced content does not belong to this course." }
```

### 2.4 Question detail with replies (numeric ID)

```
GET {{base_url}}/courses/questions/{{question_id}}/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`. Same shape as the list row plus a `replies` array.

```json
{
  "success": true,
  "data": {
    "id": 1,
    "title": "How does the event loop work?",
    "reply_count": 0,
    "upvote_count": 0,
    "replies": []
  }
}
```

### 2.5 Unenrolled learner on numeric detail → 404 (not 403)

```
GET {{base_url}}/courses/questions/{{question_id}}/
Authorization: {{stranger_token}}
```

**Expected:** `404 Not Found` (numeric ID — existence not leaked).

```json
{ "success": false, "message": "Question not found." }
```

---

## Group 3: Replies

### 3.1 Instructor reply — badged

```
POST {{base_url}}/courses/questions/{{question_id}}/replies/
Authorization: {{instructor_token}}
Content-Type: application/json

{ "body": "The event loop drains the microtask queue before macrotasks." }
```

**Expected:** `201 Created`, `is_instructor_reply: true`.

```json
{
  "success": true,
  "message": "Reply posted.",
  "data": {
    "id": 5,
    "body": "The event loop drains the microtask queue before macrotasks.",
    "author_name": "Bob Jones",
    "is_instructor_reply": true,
    "is_own": true,
    "upvote_count": 0,
    "created_at": "2026-07-22T10:05:00Z",
    "updated_at": "2026-07-22T10:05:00Z"
  }
}
```

```javascript
pm.test("instructor badge set", () => pm.expect(pm.response.json().data.is_instructor_reply).to.be.true);
pm.environment.set("reply_id", pm.response.json().data.id);
```

### 3.2 Learner reply — not badged, bumps reply_count

```
POST {{base_url}}/courses/questions/{{question_id}}/replies/
Authorization: {{learner2_token}}
Content-Type: application/json

{ "body": "I had the same doubt, thanks!" }
```

**Expected:** `201 Created`, `is_instructor_reply: false`. Re-fetch the question
(2.4) — `reply_count` is now `2`.

### 3.3 Blank reply rejected (400)

```
POST {{base_url}}/courses/questions/{{question_id}}/replies/
Authorization: {{learner_token}}
Content-Type: application/json

{ "body": "   " }
```

**Expected:** `400 Bad Request`.

---

## Group 4: Upvotes (counter-only)

### 4.1 Upvote a question

```
POST {{base_url}}/courses/questions/{{question_id}}/upvote/
Authorization: {{learner2_token}}
```

**Expected:** `200 OK`, `upvote_count: 1`.

```json
{ "success": true, "message": "Upvoted.", "data": { "upvote_count": 1 } }
```

### 4.2 Counter-only — repeat call increments again (no toggle, no dedup)

```
POST {{base_url}}/courses/questions/{{question_id}}/upvote/
Authorization: {{learner2_token}}
```

**Expected:** `200 OK`, `upvote_count: 2`. There is no un-upvote — every POST is a
`+1`, and the same caller may call it repeatedly.

```javascript
pm.test("counter incremented again", () => {
    pm.expect(pm.response.json().data.upvote_count).to.eql(2);
});
```

### 4.3 Upvote a reply

```
POST {{base_url}}/courses/replies/{{reply_id}}/upvote/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, `upvote_count` incremented by 1.

### 4.4 Upvote on a non-existent / no-access target → 404

```
POST {{base_url}}/courses/questions/99999/upvote/
Authorization: {{learner_token}}
```

**Expected:** `404 Not Found`.

### 4.5 Too many upvotes → 429

Both upvote endpoints share one per-user throttle (`DISCUSSION_UPVOTE_RATE_LIMIT`,
default `30/min`). Fire the same request in a Postman runner loop past the limit:

```
POST {{base_url}}/courses/questions/{{question_id}}/upvote/
Authorization: {{learner_token}}
```

**Expected:** `429 Too Many Requests` once the limit is hit, with a `Retry-After`
header. The body uses the standard envelope (`core.exception_handlers`), with
DRF's original `detail` kept alongside it:

```json
{
  "success": false,
  "message": "Request was throttled. Expected available in 42 seconds.",
  "detail": "Request was throttled. Expected available in 42 seconds."
}
```

---

## Group 5: Pin (instructor only)

### 5.1 Learner cannot pin → 403

```
POST {{base_url}}/courses/questions/{{question_id}}/pin/
Authorization: {{learner_token}}
```

**Expected:** `403 Forbidden`.

```json
{ "success": false, "message": "Only instructors can pin a question." }
```

### 5.2 Instructor pins → toggles

```
POST {{base_url}}/courses/questions/{{question_id}}/pin/
Authorization: {{instructor_token}}
```

**Expected:** `200 OK`, `is_pinned: true`. Call again to unpin.

```json
{ "success": true, "message": "Question pinned.", "data": { "is_pinned": true } }
```

> Pinned questions float to the top of the list regardless of `?ordering=`.

---

## Group 6: Soft Delete

### 6.1 Learner cannot delete another learner's question → 403

Post a question as `learner2_token`, then:

```
DELETE {{base_url}}/courses/questions/{{other_question_id}}/
Authorization: {{learner_token}}
```

**Expected:** `403 Forbidden`.

```json
{ "success": false, "message": "You can only delete your own question." }
```

### 6.2 Author soft-deletes own question

```
DELETE {{base_url}}/courses/questions/{{question_id}}/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`. The row stays in the DB with `is_deleted=True` and vanishes
from the list (Group 7.1) and from detail (→ 404 afterward).

### 6.3 Instructor can delete any question / reply

```
DELETE {{base_url}}/courses/questions/{{question_id}}/
Authorization: {{instructor_token}}
```
```
DELETE {{base_url}}/courses/replies/{{reply_id}}/
Authorization: {{instructor_token}}
```

**Expected:** `200 OK` for both. Deleting a reply decrements the parent's
`reply_count`.

---

## Group 7: Filtering, Ordering & Pagination

### 7.1 List excludes soft-deleted, pinned first

```
GET {{base_url}}/courses/{{course_slug}}/questions/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`. No `is_deleted` rows; any pinned question appears first.

### 7.2 Filter by content anchor

```
GET {{base_url}}/courses/{{course_slug}}/questions/?content_id={{content_id}}
Authorization: {{learner_token}}
```

**Expected:** only questions whose `related_content.id == content_id`. Invalid
`content_id` (non-numeric) → `400`.

### 7.3 Ordering

```
GET {{base_url}}/courses/{{course_slug}}/questions/?ordering=-upvote_count
Authorization: {{learner_token}}
```

Allow-listed values: `-created_at` (default), `created_at`, `-upvote_count`,
`-reply_count`. Any other value silently falls back to `-created_at`. `is_pinned`
always wins the primary sort.

### 7.4 Pagination

```
GET {{base_url}}/courses/{{course_slug}}/questions/?page=1&page_size=5
Authorization: {{learner_token}}
```

**Expected:** standard DRF page (`count`, `next`, `previous`, `results`) wrapped in
`{ "success": true, "data": { ... } }`. `page_size` max is 100.

---

## Group 8: Notifications (in-app)

These events are **in-app only** (`skip_email=True`) and fire on transaction commit.
Observe them via the notifications feed (`GET {{base_url}}/notifications/`) or the WS
`notifications` stream.

| Trigger | Event | Recipients |
|---|---|---|
| Learner posts a question | `question.posted` | Course instructors (excluding the asker) |
| Someone replies | `question.replied` | Question author + prior thread participants (excluding the replier) |

### 8.1 Question posted → instructor notified

1. `learner_token` posts a question (Group 2.1).
2. `GET {{base_url}}/notifications/` as `instructor_token`.

**Expected:** a `question.posted` notification with
`data.course_slug` + `data.question_id`.

### 8.2 Reply posted → author notified

1. `instructor_token` replies (Group 3.1).
2. `GET {{base_url}}/notifications/` as `learner_token` (the question author).

**Expected:** a `question.replied` notification; `data.is_instructor_reply` reflects
who replied.

---

## Quick Reference — Endpoints

| Method | Path | Who | Denied status |
|---|---|---|---|
| GET | `/courses/<slug>/questions/` | enrolled / instructor | slug → 403 |
| POST | `/courses/<slug>/questions/` | enrolled / instructor | slug → 403 |
| GET | `/courses/questions/<id>/` | enrolled / instructor | id → 404 |
| DELETE | `/courses/questions/<id>/` | author / instructor | id → 404, other's → 403 |
| POST | `/courses/questions/<id>/replies/` | enrolled / instructor | id → 404 |
| POST | `/courses/questions/<id>/pin/` | instructor | id → 404, learner → 403 |
| POST | `/courses/questions/<id>/upvote/` | enrolled / instructor | id → 404 |
| DELETE | `/courses/replies/<id>/` | author / instructor | id → 404, other's → 403 |
| POST | `/courses/replies/<id>/upvote/` | enrolled / instructor | id → 404 |

Upvotes are counter-only (atomic `+1`, no dedup, no un-upvote).

All responses use the standard `{ success, message, data|errors }` envelope.
