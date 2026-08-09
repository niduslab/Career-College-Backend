# Postman Guide — Learner Dashboard, Certificates List, Wishlist & Notes

Manual API testing for the new learner-facing surface above course consumption.
All endpoints are under `/api/v1/courses/`, gated
`[IsAuthenticated, IsEmailVerified, IsLearnerUser]`, and use the standard
`{ success, message, data|errors }` envelope. Design reference:
`docs/architecture/27-learner-dashboard.md`.

Covers: My Courses `?status=` filter + `status_counts`, sticky `completed_at`,
unenrolled-but-completed visibility, `GET /my-certificates/`, wishlist
add/remove/list + `is_wishlisted` catalog flag, learner notes CRUD, and the
four dashboard aggregates (summary, activity feed, upcoming, continue).

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `learner_token` | `Bearer eyJ...` | JWT, email-verified learner |
| `course_slug` | `python-fundamentals` | Published course, learner enrolled |
| `course2_slug` | `data-structures` | A second published course, not enrolled |
| `enrollment_id` | _(filled during tests)_ | PK of the learner's enrollment in `course_slug` |
| `lecture_id` | `10` | A lecture PK in `course_slug` |
| `note_id` | _(filled during tests)_ | PK of a created note |
| `certificate_uid` | _(filled during tests)_ | UUID of an issued certificate |

## Prerequisites

- A published course (`course_slug`) with ≥2 sections/lectures, and a second
  published course (`course2_slug`) the learner is **not** enrolled in.
- Learner actively enrolled in `course_slug`, with at least one completed
  lecture (`WatchProgress.is_completed=True`) so activity/summary numbers are
  non-zero.
- For the certificate tests, an enrollment with `progress_percent=100` so
  `issue_certificate` has run.

---

## Group 1: My Courses — `?status=` filter and `status_counts`

### 1.1 Default list carries `status_counts`

```
GET {{base_url}}/courses/my-courses/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`. `data` has the usual paginator keys plus `status_counts`.

```json
{
  "success": true,
  "data": {
    "count": 3, "next": null, "previous": null, "results": [ ... ],
    "status_counts": { "all": 3, "in_progress": 2, "completed": 1 }
  }
}
```

```javascript
pm.test("status_counts present", () => {
    const counts = pm.response.json().data.status_counts;
    pm.expect(counts).to.have.all.keys("all", "in_progress", "completed");
});
```

### 1.2 `?status=completed` / `?status=in_progress`

```
GET {{base_url}}/courses/my-courses/?status=completed
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, `results` contains only enrollments with
`completed_at` set. `status_counts` is unchanged — it always describes the
whole set, not the filtered page.

### 1.3 Invalid `?status=` → 400

```
GET {{base_url}}/courses/my-courses/?status=bogus
Authorization: {{learner_token}}
```

**Expected:** `400 Bad Request`.

```json
{
  "success": false,
  "message": "Invalid filter parameters.",
  "errors": { "status": "Invalid status \"bogus\". Must be one of: all, completed, in_progress." }
}
```

### 1.4 `status_counts` reflects the whole set, not one page

Enrol the learner in >10 courses (page size), mark one complete.

```
GET {{base_url}}/courses/my-courses/?page_size=5
Authorization: {{learner_token}}
```

**Expected:** `results` has 5 rows; `status_counts.all` equals the true total
enrollment count, not 5.

---

## Group 2: Completion is sticky

### 2.1 Adding a lecture after completion does not un-complete the course

1. Finish `course_slug` (complete every lecture) so `progress_percent=100`
   and `completed_at` is set. Confirm via 1.2 (`?status=completed`).
2. As an instructor/admin, add one new lecture to the course.
3. Trigger a recalc — e.g. re-fetch any lecture in the course
   (`GET {{base_url}}/courses/lectures/{{lecture_id}}/`), which reads
   `WatchProgress` and fires the post-save signal.
4. Repeat 1.2.

**Expected:** the course still appears under `?status=completed`;
`completed_at` is unchanged. `progress_percent` drops below 100 to reflect
the new, larger denominator — course now also shows an incomplete item.

### 2.2 Unenrolling from a completed course keeps it in My Courses

1. With the course from 2.1 still completed, unenroll:
   ```
   POST {{base_url}}/courses/{{course_slug}}/unenroll/
   Authorization: {{learner_token}}
   ```
2. Repeat 1.1 and 1.2.

**Expected:** the course is **absent** from the default `is_active=True` scan
of other endpoints (e.g. `learner/continue/`, see 6.4) but **still appears**
in `GET /my-courses/` and under `?status=completed`; `status_counts.completed`
is unchanged. Its certificate (Group 3) is still downloadable.

### 2.3 Unenrolling from an in-progress (unfinished) course hides it

Unenroll from a course that was never completed.

**Expected:** it drops out of `GET /my-courses/` entirely — the
`include_unenrolled_completed` widening only applies to finished courses.

---

## Group 3: Certificates List

### 3.1 List the learner's certificates

```
GET {{base_url}}/courses/my-certificates/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, paginated, newest first.

```json
{
  "success": true,
  "data": {
    "count": 1, "next": null, "previous": null,
    "results": [
      {
        "certificate_uid": "b3f1...-uuid",
        "learner_name": "Alice Smith",
        "course_title": "Python Fundamentals",
        "issued_at": "2026-07-01T12:00:00Z",
        "course": { "id": 12, "title": "Python Fundamentals", "slug": "python-fundamentals", "thumbnail": null },
        "download_url": "/api/v1/courses/certificates/b3f1.../download/",
        "verify_url": "/api/v1/courses/certificates/b3f1.../verify/"
      }
    ]
  }
}
```

```javascript
pm.test("save certificate_uid", () => {
    pm.environment.set("certificate_uid", pm.response.json().data.results[0].certificate_uid);
});
pm.test("course_title and course.title both present", () => {
    const row = pm.response.json().data.results[0];
    pm.expect(row.course_title).to.be.a("string");
    pm.expect(row.course.title).to.be.a("string");
});
```

### 3.2 `course_title` (frozen) vs `course.title` (live) diverge after a rename

1. Note `course_title` from 3.1.
2. As instructor, rename the course.
3. Repeat 3.1.

**Expected:** `course_title` is unchanged (the snapshot frozen at issue);
`course.title` reflects the new name.

### 3.3 `download_url` / `verify_url` resolve

```
GET {{base_url}}{{download_url}}
```
```
GET {{base_url}}{{verify_url}}
```

**Expected:** both `200 OK` — same behaviour as the existing
`certificates/<uid>/download/` and `/verify/` endpoints
(`postman-certificate.md`), just linked from the list row.

---

## Group 4: Wishlist

### 4.1 Add a course to the wishlist — first time (201)

```
POST {{base_url}}/courses/{{course2_slug}}/wishlist/
Authorization: {{learner_token}}
```

**Expected:** `201 Created`.

```json
{
  "success": true,
  "message": "Course saved to your wishlist.",
  "data": { "id": 5, "course": { "id": 20, "slug": "data-structures", "is_wishlisted": true, ... }, "created_at": "2026-08-09T10:00:00Z" }
}
```

### 4.2 Add again — idempotent (200, not a second row)

```
POST {{base_url}}/courses/{{course2_slug}}/wishlist/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, `"message": "Course is already on your wishlist."`.
Confirm no duplicate row via 4.4 (`count` still 1).

### 4.3 Add unpublished / non-existent course → 404

```
POST {{base_url}}/courses/no-such-course/wishlist/
Authorization: {{learner_token}}
```

**Expected:** `404 Not Found`.

### 4.4 List the wishlist

```
GET {{base_url}}/courses/wishlist/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, paginated, most-recently-saved first, each row's
nested `course.is_wishlisted` is `true`.

### 4.5 `is_wishlisted` on catalog list and detail

```
GET {{base_url}}/courses/catalog/?search=data
Authorization: {{learner_token}}
```

**Expected:** the row for `course2_slug` has `"is_wishlisted": true`; other
rows `false`.

```
GET {{base_url}}/courses/catalog/{{course2_slug}}/
Authorization: {{learner_token}}
```

**Expected:** `data.is_wishlisted: true`.

### 4.6 Anonymous catalog browse — flag defaults false, no error

```
GET {{base_url}}/courses/catalog/
```
(no `Authorization` header)

**Expected:** `200 OK`, every row `"is_wishlisted": false`.

### 4.7 Remove from wishlist

```
DELETE {{base_url}}/courses/{{course2_slug}}/wishlist/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`. Repeat 4.4 — row gone. Repeat 4.5 — `is_wishlisted`
back to `false`.

### 4.8 Remove when not wishlisted → 404

```
DELETE {{base_url}}/courses/{{course2_slug}}/wishlist/
Authorization: {{learner_token}}
```

**Expected:** `404 Not Found`, `"message": "Course is not on your wishlist."`.

---

## Group 5: Learner Notes

### 5.1 Create a note anchored to a lecture + timestamp

```
POST {{base_url}}/courses/notes/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "lecture_id": {{lecture_id}},
  "timestamp_seconds": 125,
  "title": "Closures",
  "body": "Remember: the inner function captures the outer scope by reference.",
  "tags": ["Python", "closures", "python"],
  "color": "yellow"
}
```

**Expected:** `201 Created`. `tags` deduped/lowercased to `["python", "closures"]`
(insertion order kept, case-insensitive dup dropped); `course` derived from
the lecture.

```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.test("tags deduped and lowercased", () => {
    pm.expect(pm.response.json().data.tags).to.eql(["python", "closures"]);
});
pm.environment.set("note_id", pm.response.json().data.id);
```

### 5.2 Timestamp without a lecture → 400

```
POST {{base_url}}/courses/notes/
Authorization: {{learner_token}}
Content-Type: application/json

{ "timestamp_seconds": 30, "body": "orphan timestamp" }
```

**Expected:** `400 Bad Request` — serializer-level rejection
(`timestamp_seconds requires lecture_id`).

### 5.3 Note without any anchor — allowed

```
POST {{base_url}}/courses/notes/
Authorization: {{learner_token}}
Content-Type: application/json

{ "body": "General reminder, not tied to any course." }
```

**Expected:** `201 Created`, `course: null`, `lecture: null`. Enrollment is
not required to file a note.

### 5.4 Blank body → 400 (serializer + DB constraint)

```
POST {{base_url}}/courses/notes/
Authorization: {{learner_token}}
Content-Type: application/json

{ "body": "" }
```

**Expected:** `400 Bad Request`.

### 5.5 Lecture/course mismatch → 400

```
POST {{base_url}}/courses/notes/
Authorization: {{learner_token}}
Content-Type: application/json

{ "course_slug": "{{course2_slug}}", "lecture_id": {{lecture_id}}, "body": "mismatched anchor" }
```

Where `lecture_id` belongs to `course_slug`, not `course2_slug`.

**Expected:** `400 Bad Request`, `"message": "Lecture does not belong to that course."`.

### 5.6 List, filter by course/tag/pinned, search

```
GET {{base_url}}/courses/notes/?course={{course_slug}}&tag=python&is_pinned=false&search=closure
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, only notes matching all filters. Multiple `?tag=`
values AND together (a note needs every tag listed).

### 5.7 Invalid `?ordering=` / `?lecture_id=` / `?is_pinned=` → 400 with all field errors

```
GET {{base_url}}/courses/notes/?ordering=bogus&lecture_id=abc&is_pinned=maybe
Authorization: {{learner_token}}
```

**Expected:** `400 Bad Request`, `errors` carries all three field messages at once.

### 5.8 Get / patch / delete own note

```
GET {{base_url}}/courses/notes/{{note_id}}/
Authorization: {{learner_token}}
```
```
PATCH {{base_url}}/courses/notes/{{note_id}}/
Authorization: {{learner_token}}
Content-Type: application/json

{ "is_pinned": true, "color": "green" }
```
```
DELETE {{base_url}}/courses/notes/{{note_id}}/
Authorization: {{learner_token}}
```

**Expected:** `200 OK` for all three. PATCH is partial — omitted fields
untouched. DELETE is a hard delete; a repeat GET → 404.

### 5.9 Another learner's note → 404, never 403

Using a second learner's token against `note_id` from 5.1 (before deleting it):

```
GET {{base_url}}/courses/notes/{{note_id}}/
Authorization: {{learner2_token}}
```

**Expected:** `404 Not Found` — numeric IDs are not enumerable, ownership
mismatch reads identically to "doesn't exist."

---

## Group 6: Learner Dashboard Aggregates

### 6.1 Summary KPIs

```
GET {{base_url}}/courses/learner/dashboard/summary/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`. No `total_xp` key at all (not zero — absent).

```json
{
  "success": true,
  "message": "Dashboard summary retrieved.",
  "data": {
    "courses_enrolled": 3,
    "courses_in_progress": 2,
    "courses_completed": 1,
    "certificates_earned": 1,
    "average_progress_percent": 64.3,
    "total_learning_seconds": 5400,
    "total_learning_hours": 1.5,
    "lectures_completed": 12,
    "day_streak": 4,
    "day_streak_is_approximate": false,
    "day_streak_timezone": "Asia/Dhaka"
  }
}
```

```javascript
pm.test("no total_xp field", () => {
    pm.expect(pm.response.json().data).to.not.have.property("total_xp");
});
```

### 6.2 Day streak increments on studying, not on browsing

1. Note `day_streak` from 6.1.
2. `GET {{base_url}}/courses/catalog/` (browsing only — no content read).
3. Repeat 6.1 — streak unchanged (dashboard/catalog browsing doesn't count).
4. Open a lecture: `GET {{base_url}}/courses/lectures/{{lecture_id}}/`.
5. Repeat 6.1 — if this is the first study action today, `day_streak`
   increments by exactly 1; repeat opens the same day do not double-count.

### 6.3 Activity feed — merged, newest first, filterable by type

```
GET {{base_url}}/courses/learner/activity/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, paginated, rows sorted by `occurred_at` DESC across
all six source types.

```json
{
  "success": true,
  "data": {
    "count": 42, "next": "...", "previous": null,
    "results": [
      {
        "id": "quiz:88",
        "type": "quiz_submitted",
        "occurred_at": "2026-08-09T09:00:00Z",
        "title": "Chapter 3 Quiz",
        "course": { "id": 12, "title": "Python Fundamentals", "slug": "python-fundamentals", "thumbnail": null },
        "meta": { "quiz_id": 7, "attempt_id": 88, "score": 8, "max_score": 10 }
      }
    ]
  }
}
```

```
GET {{base_url}}/courses/learner/activity/?type=certificate_earned,course_enrolled
Authorization: {{learner_token}}
```

**Expected:** only rows of those two types.

```
GET {{base_url}}/courses/learner/activity/?type=bogus
Authorization: {{learner_token}}
```

**Expected:** `400 Bad Request`, `errors.type` lists the invalid value(s) and
the valid set.

### 6.4 Continue learning — resume target

```
GET {{base_url}}/courses/learner/continue/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, resolves to the most-recently-accessed **active**
enrollment.

```json
{
  "success": true,
  "message": "Continue target retrieved.",
  "data": {
    "enrollment": { "id": 4, "progress_percent": 40, "last_accessed_at": "2026-08-09T08:00:00Z", "completed_at": null },
    "course": { "id": 12, "title": "Python Fundamentals", "slug": "python-fundamentals", "thumbnail": null },
    "next_lecture": { "lecture_id": 15, "content_id": 30, "title": "Closures", "lecture_type": "video", "duration_seconds": 600, "section": { "id": 3, "title": "Functions", "position": 2 } },
    "is_course_complete": false,
    "locked_until": null
  }
}
```

**No active enrollment → 200 with `data: null`, never 404:**

```
GET {{base_url}}/courses/learner/continue/
Authorization: {{stranger_token}}
```

```json
{ "success": true, "message": "Continue target retrieved.", "data": null }
```

### 6.5 Continue never targets an unenrolled-but-completed course

Using the learner from Group 2.2 (unenrolled from their one completed
course, no other active enrollments):

```
GET {{base_url}}/courses/learner/continue/
Authorization: {{learner_token}}
```

**Expected:** `data: null` — the completed-but-unenrolled course must not be
offered as a resume target even though it still shows in My Courses.

### 6.6 Upcoming — cohort/drip/webinar dates

```
GET {{base_url}}/courses/learner/upcoming/?days=60&limit=10
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, `items` sorted by `occurs_at` ascending, mixing
`course_starts` / `course_ends` / `section_unlocks` / `webinar_starts`.

```json
{
  "success": true,
  "data": {
    "horizon_days": 60,
    "count": 2,
    "items": [
      { "type": "section_unlocks", "occurs_at": "2026-08-15T00:00:00Z", "title": "Week 3", "subtitle": "Python Fundamentals", "course": { "...": "..." }, "webinar": null, "meta": { "section_id": 9 } },
      { "type": "webinar_starts", "occurs_at": "2026-08-20T18:00:00Z", "title": "Live AI Workshop", "subtitle": null, "course": null, "webinar": { "id": 3, "title": "Live AI Workshop", "slug": "live-ai-workshop" }, "meta": { "registration_id": 12 } }
    ]
  }
}
```

### 6.7 `?days=` / `?limit=` bounds

```
GET {{base_url}}/courses/learner/upcoming/?days=9999
Authorization: {{learner_token}}
```

**Expected:** `200 OK`, silently clamped — `horizon_days: 365` (max), not an error.

```
GET {{base_url}}/courses/learner/upcoming/?days=0
Authorization: {{learner_token}}
```

**Expected:** `400 Bad Request`, `errors.days: "Must be at least 1."`.

---

## Quick Reference — Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/courses/my-courses/?status=` | `all`\|`in_progress`\|`completed`; adds `status_counts` |
| GET | `/courses/my-certificates/` | Paginated, newest first |
| GET | `/courses/wishlist/` | Paginated |
| POST/DELETE | `/courses/<slug>/wishlist/` | 201 first add, 200 repeat, 404 remove-when-absent |
| GET/POST | `/courses/notes/` | List (filterable) / create |
| GET/PATCH/DELETE | `/courses/notes/<id>/` | Owner only, 404 on mismatch |
| GET | `/courses/learner/dashboard/summary/` | KPI tiles, no `total_xp` |
| GET | `/courses/learner/activity/?type=` | Paginated, 6 merged sources |
| GET | `/courses/learner/upcoming/?days=&limit=` | Not paginated, clamped bounds |
| GET | `/courses/learner/continue/` | `data: null` when no active enrollment |

All responses use the standard `{ success, message, data|errors }` envelope.
