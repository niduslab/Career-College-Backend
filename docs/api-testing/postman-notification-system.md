# Postman Guide — Notification System

Manual API testing for the full notification system: bell feed, mark-read, unread count, user preferences, real-time WebSocket push, and email delivery.

---

## Environment Variables

Set these in your Postman environment before running any request.

| Variable | Example value | Notes |
|---|---|---|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `ws_url` | `ws://localhost:8000/ws` | WebSocket base URL |
| `learner_token` | `Bearer eyJ...` | JWT for an active learner |
| `instructor_token` | `Bearer eyJ...` | JWT for a verified instructor |
| `admin_token` | `Bearer eyJ...` | JWT for a platform admin |
| `learner_raw_token` | `eyJ...` | Same JWT without the `Bearer ` prefix — used in WS URL |
| `course_slug` | `intro-to-django` | Slug of a published course |
| `notification_id` | _(filled during tests)_ | ID of a notification row |
| `submission_id` | _(filled during tests)_ | Assignment submission ID |

---

## Prerequisites

1. Django dev server running (`python manage.py runserver`).
2. Redis running (required for Celery and Django Channels).
3. Celery worker running with the `notifications` queue:
   ```bash
   celery -A career_college_backend worker -Q celery,notifications -l info
   ```
4. ASGI server required for WebSocket support. Run with Daphne or Uvicorn instead of the standard `runserver`:
   ```bash
   pip install daphne
   daphne -p 8000 career_college_backend.asgi:application
   ```
   > **Note:** Standard `python manage.py runserver` does not serve WebSocket connections. Use Daphne or Uvicorn for WS testing.
5. `EMAIL_BACKEND = django.core.mail.backends.console.EmailBackend` in settings for local dev — emails print to the terminal.
6. At least one published course with a learner enrolled in it.

---

## Part 1 — Bell Feed (REST API)

### 1.1 — List all notifications

```
GET {{base_url}}/notifications/
Authorization: {{learner_token}}
```

**Expected response (200):**
```json
{
  "success": true,
  "data": {
    "count": 5,
    "next": null,
    "previous": null,
    "results": [
      {
        "id": 42,
        "event_type": "enrollment.created",
        "title": "Enrolled in Intro to Django",
        "body": "You have successfully enrolled in \"Intro to Django\".",
        "data": { "course_slug": "intro-to-django" },
        "is_read": false,
        "read_at": null,
        "created_at": "2026-06-14T10:00:00Z"
      }
    ]
  }
}
```

**Save `data.results[0].id` as `notification_id` for later steps.**

---

### 1.2 — Filter to unread only

```
GET {{base_url}}/notifications/?is_read=false
Authorization: {{learner_token}}
```

**Expected:** Only rows where `is_read = false`.

---

### 1.3 — Filter by event type

```
GET {{base_url}}/notifications/?event_type=enrollment.created
Authorization: {{learner_token}}
```

**Expected:** Only `enrollment.created` rows.

---

### 1.4 — Pagination

```
GET {{base_url}}/notifications/?page_size=2&page=1
Authorization: {{learner_token}}
```

**Expected:** `data.results` has at most 2 items. `data.next` is non-null if more pages exist.

---

### 1.5 — Get unread count

```
GET {{base_url}}/notifications/unread-count/
Authorization: {{learner_token}}
```

**Expected (200):**
```json
{
  "success": true,
  "data": { "count": 5 }
}
```

---

### 1.6 — Mark specific notifications as read

```
POST {{base_url}}/notifications/mark-read/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "ids": [{{notification_id}}]
}
```

**Expected (200):**
```json
{ "success": true, "message": "Marked as read." }
```

**Verify:** Call `GET /notifications/unread-count/` again — count should decrease by 1. Call `GET /notifications/?is_read=false` — the marked ID should not appear.

---

### 1.7 — Mark all notifications as read

```
POST {{base_url}}/notifications/mark-read/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "all": true
}
```

**Expected (200):**
```json
{ "success": true, "message": "Marked as read." }
```

**Verify:** `GET /notifications/unread-count/` returns `{ "count": 0 }`.

---

### 1.8 — Pass another user's notification IDs (security check)

```
POST {{base_url}}/notifications/mark-read/
Authorization: {{instructor_token}}
Content-Type: application/json

{
  "ids": [{{notification_id}}]
}
```

**Expected (200):** Success response with 0 rows actually updated — IDs belonging to a different user are silently ignored, not an error.

**Verify:** Call `GET /notifications/` as the learner — the notification is still unread.

---

### 1.9 — Unauthenticated request

```
GET {{base_url}}/notifications/
```

**Expected (401):** Authentication credentials not provided.

---

## Part 2 — User Preferences

### 2.1 — Get all preferences

```
GET {{base_url}}/notifications/preferences/
Authorization: {{learner_token}}
```

**Expected (200):**
```json
{
  "success": true,
  "data": [
    { "category": "course_activity",   "email_enabled": true, "push_enabled": true },
    { "category": "assessments",       "email_enabled": true, "push_enabled": true },
    { "category": "course_management", "email_enabled": true, "push_enabled": true },
    { "category": "collaboration",     "email_enabled": true, "push_enabled": true },
    { "category": "verification",      "email_enabled": true, "push_enabled": true }
  ]
}
```

> First call creates the preference rows lazily — all default to `true`.

---

### 2.2 — Disable email for one category

```
PATCH {{base_url}}/notifications/preferences/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "assessments": { "email_enabled": false }
}
```

**Expected (200):**
```json
{
  "success": true,
  "message": "Preferences updated.",
  "data": [ ... ]
}
```

**Verify:** `assessments.email_enabled` is `false` in the returned data. Call `GET /preferences/` again — same value persists.

---

### 2.3 — Disable multiple categories at once

```
PATCH {{base_url}}/notifications/preferences/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "assessments":    { "email_enabled": false },
  "collaboration":  { "email_enabled": false }
}
```

**Expected (200):** Both categories updated in a single request.

---

### 2.4 — Unknown category (validation error)

```
PATCH {{base_url}}/notifications/preferences/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "not_a_real_category": { "email_enabled": false }
}
```

**Expected (400):** Validation error — `not_a_real_category` is not a valid category.

---

## Part 3 — Triggering Notifications

Use these flows to generate notification rows so the REST endpoints have real data to return.

### 3.1 — `enrollment.created` (learner gets in-app + email)

```
POST {{base_url}}/courses/{{course_slug}}/enroll/
Authorization: {{learner_token}}
```

**Expected side effects:**
- `Notification` row created for the learner (`event_type = enrollment.created`).
- `Notification` rows created for all course instructors (`event_type = learner.enrolled`, WS-only).
- Email sent to learner (check terminal if using console backend).
- `GET /notifications/unread-count/` as learner returns count increased by 1.

---

### 3.2 — `course.completed` (learner gets in-app + email)

Force progress to 100% by completing all lectures and quizzes in the course. When `recalculate_progress()` transitions to 100%:

**Expected side effects:**
- `Notification` row created for the learner (`event_type = course.completed`).
- Certificate issued (check `GET /courses/certificates/my/`).
- Email sent to learner with certificate link (check terminal).

---

### 3.5 — `course.submitted_for_review` (admins get in-app + email)

```
POST {{base_url}}/courses/{{course_slug}}/submit/
Authorization: {{instructor_token}}
```

**Expected side effects:**
- `Notification` rows created for all admin users (`event_type = course.submitted_for_review`).
- Email sent to all admins (check terminal).

---

### 3.6 — `course.approved` + `course.rejected`

Approve a submitted course:
```
POST {{base_url}}/courses/{{course_slug}}/review/
Authorization: {{admin_token}}
Content-Type: application/json

{ "action": "approve" }
```

**Expected side effects:**
- `Notification` rows created for all course instructors (`event_type = course.approved`).
- Email sent to all instructors.

Reject instead:
```json
{ "action": "reject", "rejection_reason": "Missing prerequisites section." }
```

**Expected:** `course.rejected` notification + email to instructors.

---

### 3.7 — `invite.sent` / `invite.accepted` / `invite.declined`

Send invite:
```
POST {{base_url}}/courses/{{course_slug}}/instructors/invite/
Authorization: {{instructor_token}}
Content-Type: application/json

{ "email": "other_instructor@example.com" }
```

**Expected:** `invite.sent` notification + email to the invitee.

Accept invite:
```
POST {{base_url}}/courses/invites/{{invite_token}}/accept/
Authorization: {{other_instructor_token}}
```

**Expected:** `invite.accepted` notification + email to the original inviter.

---

### 3.8 — Verification events

Submit for verification:
```
POST {{base_url}}/verification/{{verification_id}}/submit/
Authorization: {{instructor_token}}
```

**Expected:** `verification.submitted` notification + email to all admins.

Admin approves:
```
POST {{base_url}}/verification/admin/{{verification_id}}/review/
Authorization: {{admin_token}}
Content-Type: application/json

{ "action": "approve" }
```

**Expected:** `verification.approved` notification + email to the instructor.

---

## Part 4 — WebSocket Testing

Use [wscat](https://github.com/websockets/wscat) (`npm install -g wscat`) or the Postman WebSocket feature.

### 4.1 — Connect and receive unread count

```bash
wscat -c "ws://localhost:8000/ws/?token={{learner_raw_token}}"
```

**Expected immediately on connect:**
```json
{ "stream": "notifications", "payload": { "type": "unread_count", "count": 3 } }
```

> Use the raw JWT token (no `Bearer ` prefix) in the URL.

---

### 4.2 — Receive a live notification

With the WS connection open, trigger an event in another terminal or Postman tab (e.g. enroll the learner in a new course via a second account).

**Expected on the open WS connection:**
```json
{
  "stream": "notifications",
  "payload": {
    "type": "notification",
    "id": 99,
    "event_type": "enrollment.created",
    "title": "Enrolled in Intro to Django",
    "body": "You have successfully enrolled in \"Intro to Django\".",
    "data": { "course_slug": "intro-to-django" },
    "is_read": false,
    "created_at": "2026-06-14T11:00:00Z"
  }
}
```

---

### 4.3 — Mark specific notifications as read via WebSocket

```json
{ "stream": "notifications", "payload": { "type": "mark_read", "ids": [99] } }
```

**Expected response:**
```json
{ "stream": "notifications", "payload": { "type": "unread_count", "count": 2 } }
```

**Verify:** `GET /notifications/unread-count/` via REST also returns 2.

---

### 4.4 — Mark all as read via WebSocket

```json
{ "stream": "notifications", "payload": { "type": "mark_all_read" } }
```

**Expected:**
```json
{ "stream": "notifications", "payload": { "type": "unread_count", "count": 0 } }
```

---

### 4.5 — Send to messaging stream (stub — no-op)

```json
{ "stream": "messaging", "payload": { "type": "anything" } }
```

**Expected:** No error, no response. Connection stays open. The messaging stream is a registered stub.

---

### 4.6 — Send unknown stream

```json
{ "stream": "unknown_stream", "payload": {} }
```

**Expected:**
```json
{ "stream": "error", "payload": { "detail": "Unknown stream: unknown_stream." } }
```

Connection stays open.

---

### 4.7 — Connect without token (auth failure)

```bash
wscat -c "ws://localhost:8000/ws/"
```

**Expected:** Connection closed immediately with code `4001`.

---

### 4.8 — Connect with an invalid/expired token

```bash
wscat -c "ws://localhost:8000/ws/?token=not.a.real.token"
```

**Expected:** Connection closed immediately with code `4001`.

---

## Part 5 — Email Delivery Verification

For local dev with `EMAIL_BACKEND = django.core.mail.backends.console.EmailBackend`, emails print to the Django/Daphne terminal.

### Events that send email (12 total)

| Event | Recipient |
|---|---|
| `enrollment.created` | Learner |
| `course.completed` | Learner |
| `course.submitted_for_review` | All admins |
| `course.approved` | All course instructors |
| `course.rejected` | All course instructors |
| `invite.sent` | Invitee |
| `invite.accepted` | Inviter |
| `invite.declined` | Inviter |
| `verification.submitted` | All admins |
| `verification.approved` | Instructor |
| `verification.rejected` | Instructor |
| `verification.action_required` | Instructor |

### Events that do NOT send email (6 WS-only)

`lecture.completed`, `video.transcoding_completed`, `video.transcoding_failed`, `review.received`, `learner.enrolled`, `course.archived`

### Check email suppression via preferences

1. Turn off email for `course_activity`:
   ```
   PATCH /notifications/preferences/
   { "course_activity": { "email_enabled": false } }
   ```
2. Enroll the learner in a new course.
3. Check terminal — no `enrollment.created` email should appear.
4. `GET /notifications/` — notification row still created (bell feed unaffected).
5. Re-enable: `{ "course_activity": { "email_enabled": true } }` and repeat to confirm email returns.

---

## Part 6 — Edge Cases

### 6.1 — Offline user misses WS push but sees it in bell feed

1. Disconnect wscat.
2. Trigger an event (e.g. enroll in a course).
3. Reconnect wscat — check the unread count on connect reflects the new notification.
4. `GET /notifications/` — the notification row exists.

---

### 6.2 — Unenrolled user cannot access bell feed endpoints

```
GET {{base_url}}/notifications/
```
_(no Authorization header)_

**Expected (401).**

---

### 6.3 — Mark-read with empty ids list

```
POST {{base_url}}/notifications/mark-read/
Authorization: {{learner_token}}
Content-Type: application/json

{ "ids": [] }
```

**Expected (400):** Validation error — `ids` must not be empty.

---

### 6.4 — Mark-read with neither ids nor all

```
POST {{base_url}}/notifications/mark-read/
Authorization: {{learner_token}}
Content-Type: application/json

{}
```

**Expected (400):** Validation error — provide `ids` or `all: true`.
