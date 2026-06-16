# Postman Guide — Messaging System

Manual API testing for the full messaging system: conversation creation, message threads, mark-read, real-time WebSocket delivery, and notification integration.

---

## Environment Variables

Set these in your Postman environment before running the collection.

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `ws_url` | `ws://localhost:8000/ws` | WebSocket base URL |
| `learner_token` | `Bearer eyJ...` | JWT for a learner enrolled in the test course |
| `learner_raw_token` | `eyJ...` | Same JWT without the `Bearer ` prefix — used in WS URL |
| `learner2_token` | `Bearer eyJ...` | JWT for a second learner (not enrolled) — used for access-denial checks |
| `instructor_token` | `Bearer eyJ...` | JWT for an instructor in the test course |
| `course_id` | `1` | PK of a published course |
| `instructor_id` | `2` | PK of an instructor in `course.instructors.all()` |
| `conversation_id` | _(filled during tests)_ | ID of the conversation row |

---

## Prerequisites

1. Django dev server running (`python manage.py runserver`).
2. Redis running (required for Django Channels WS push and Celery).
3. ASGI server required for WebSocket support. Run with Daphne instead of standard `runserver`:
   ```bash
   pip install daphne
   daphne -p 8000 career_college_backend.asgi:application
   ```
   > **Note:** Standard `python manage.py runserver` does not serve WebSocket connections. Use Daphne for WS testing.
4. Celery worker running (required for notification emails):
   ```bash
   celery -A career_college_backend worker -Q celery,notifications -l info
   ```
5. `EMAIL_BACKEND = django.core.mail.backends.console.EmailBackend` in settings — emails print to the terminal.
6. At least one published course with:
   - A learner actively enrolled (`Enrollment.is_active = True`).
   - At least one instructor in `course.instructors.all()`.
   - Note the course `pk` as `course_id` and instructor `pk` as `instructor_id`.

---

## Group 1: Starting a Conversation

### 1.1 Learner starts a conversation — happy path

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "course_id": {{course_id}},
  "instructor_id": {{instructor_id}},
  "body": "Hi, I have a question about lecture 3."
}
```

**Expected:** `201 Created`.

```json
{
  "success": true,
  "message": "Conversation started.",
  "data": {
    "id": 1,
    "learner_id": 3,
    "learner_name": "Alice Smith",
    "instructor_id": 2,
    "instructor_name": "Bob Jones",
    "course_title": "Python Fundamentals",
    "course_slug": "python-fundamentals",
    "unread_count": 0,
    "updated_at": "2026-06-16T10:00:00Z",
    "created_at": "2026-06-16T10:00:00Z"
  }
}
```

**Postman Test — save conversation_id:**
```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.test("conversation id present", () => {
    const id = pm.response.json().data.id;
    pm.expect(id).to.be.a("number");
    pm.environment.set("conversation_id", id);
});
pm.test("unread_count is 0 on creation", () => {
    pm.expect(pm.response.json().data.unread_count).to.equal(0);
});
```

---

### 1.2 Same request again — idempotency

Re-send the identical request from 1.1.

**Expected:** `200 OK`.

```json
{
  "success": true,
  "message": "Conversation already exists.",
  "data": { ... }
}
```

**Postman Test:**
```javascript
pm.test("200 on duplicate, not 201", () => pm.response.to.have.status(200));
pm.test("same conversation id returned", () => {
    pm.expect(pm.response.json().data.id).to.equal(
        pm.environment.get("conversation_id") * 1
    );
});
```

No duplicate conversation created. No extra messages created.

---

### 1.3 Instructor cannot initiate a conversation — 403

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{instructor_token}}
Content-Type: application/json

{
  "course_id": {{course_id}},
  "instructor_id": {{instructor_id}},
  "body": "Hello learner"
}
```

**Expected:** `403 Forbidden`.

**Postman Test:**
```javascript
pm.test("instructor blocked from initiating", () => pm.response.to.have.status(403));
```

---

### 1.4 Unenrolled learner blocked — 403

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner2_token}}
Content-Type: application/json

{
  "course_id": {{course_id}},
  "instructor_id": {{instructor_id}},
  "body": "Can I message?"
}
```

**Expected:** `403 Forbidden`.

```json
{
  "success": false,
  "message": "You must be actively enrolled in this course to message an instructor."
}
```

---

### 1.5 Non-existent course — 404

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "course_id": 99999,
  "instructor_id": {{instructor_id}},
  "body": "Hello"
}
```

**Expected:** `404 Not Found`, `message: "Course not found."`.

---

### 1.6 Non-existent instructor — 404

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "course_id": {{course_id}},
  "instructor_id": 99999,
  "body": "Hello"
}
```

**Expected:** `404 Not Found`, `message: "Instructor not found."`.

---

### 1.7 Blank body rejected — 400

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "course_id": {{course_id}},
  "instructor_id": {{instructor_id}},
  "body": "   "
}
```

**Expected:** `400 Bad Request`.

```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "body": ["Message body must not be blank."]
  }
}
```

---

## Group 2: Listing Conversations

### 2.1 Learner lists their conversations

```
GET {{base_url}}/messaging/conversations/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`.

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
        "learner_id": 3,
        "learner_name": "Alice Smith",
        "instructor_id": 2,
        "instructor_name": "Bob Jones",
        "course_title": "Python Fundamentals",
        "course_slug": "python-fundamentals",
        "unread_count": 0,
        "updated_at": "2026-06-16T10:00:00Z",
        "created_at": "2026-06-16T10:00:00Z"
      }
    ]
  }
}
```

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("conversation appears in inbox", () => {
    const results = pm.response.json().data.results;
    pm.expect(results.length).to.be.at.least(1);
    pm.expect(results[0].id).to.equal(pm.environment.get("conversation_id") * 1);
});
```

---

### 2.2 Instructor lists their conversations

```
GET {{base_url}}/messaging/conversations/
Authorization: {{instructor_token}}
```

**Expected:** `200 OK`. Same conversation row appears in the instructor's inbox from their perspective.

---

### 2.3 Pagination

```
GET {{base_url}}/messaging/conversations/?page=1&page_size=5
Authorization: {{learner_token}}
```

**Expected:** `data.results` has at most 5 items. `data.next` is non-null if more pages exist.

---

### 2.4 Unauthenticated request — 401

```
GET {{base_url}}/messaging/conversations/
```

**Expected:** `401 Unauthorized`.

---

## Group 3: Conversation Detail and Messages

### 3.1 Learner gets conversation detail

```
GET {{base_url}}/messaging/conversations/{{conversation_id}}/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`.

```json
{
  "success": true,
  "data": {
    "conversation": {
      "id": 1,
      "learner_id": 3,
      "learner_name": "Alice Smith",
      "instructor_id": 2,
      "instructor_name": "Bob Jones",
      "course_title": "Python Fundamentals",
      "course_slug": "python-fundamentals",
      "unread_count": 0,
      "updated_at": "2026-06-16T10:00:00Z",
      "created_at": "2026-06-16T10:00:00Z"
    },
    "messages": {
      "count": 1,
      "next": null,
      "previous": null,
      "results": [
        {
          "id": 1,
          "conversation_id": 1,
          "sender_id": 3,
          "sender_name": "Alice Smith",
          "body": "Hi, I have a question about lecture 3.",
          "is_own": true,
          "created_at": "2026-06-16T10:00:00Z"
        }
      ]
    }
  }
}
```

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("is_own is true for own message", () => {
    const msg = pm.response.json().data.messages.results[0];
    pm.expect(msg.is_own).to.equal(true);
});
pm.test("is_deleted not in payload", () => {
    const msg = pm.response.json().data.messages.results[0];
    pm.expect(msg).to.not.have.property("is_deleted");
});
```

---

### 3.2 Instructor gets conversation detail

Same request with `{{instructor_token}}`.

**Expected:** `200 OK`. Same structure. `is_own` is `false` for the learner's opener message since the instructor didn't send it.

**Postman Test:**
```javascript
pm.test("200 ok for instructor", () => pm.response.to.have.status(200));
pm.test("is_own is false for learner's message", () => {
    const msg = pm.response.json().data.messages.results[0];
    pm.expect(msg.is_own).to.equal(false);
});
```

---

### 3.3 Outsider cannot access conversation — 404

```
GET {{base_url}}/messaging/conversations/{{conversation_id}}/
Authorization: {{learner2_token}}
```

**Expected:** `404 Not Found`, `message: "Conversation not found."`.

**Postman Test:**
```javascript
pm.test("outsider gets 404 not 403", () => pm.response.to.have.status(404));
pm.test("message does not reveal existence", () => {
    pm.expect(pm.response.json().message).to.equal("Conversation not found.");
});
```

> Numeric IDs return 404 (not 403) on no-access — resource existence is not leaked. See [CLAUDE.md](../../CLAUDE.md).

---

### 3.4 Paginate messages

```
GET {{base_url}}/messaging/conversations/{{conversation_id}}/?page=1&page_size=20
Authorization: {{learner_token}}
```

**Expected:** `data.messages.results` has at most 20 items, ordered oldest-first.

---

## Group 4: Sending Messages

### 4.1 Instructor replies — happy path

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/messages/
Authorization: {{instructor_token}}
Content-Type: application/json

{
  "body": "Great question! The answer is in section 3.2."
}
```

**Expected:** `201 Created`.

```json
{
  "success": true,
  "message": "Message sent.",
  "data": {
    "id": 2,
    "conversation_id": 1,
    "sender_id": 2,
    "sender_name": "Bob Jones",
    "body": "Great question! The answer is in section 3.2.",
    "is_own": true,
    "created_at": "2026-06-16T10:05:00Z"
  }
}
```

**Postman Test:**
```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.test("is_own true for sender", () => {
    pm.expect(pm.response.json().data.is_own).to.equal(true);
});
```

**Verify:** `GET /messaging/conversations/{{conversation_id}}/` — `data.messages.count` is now 2.

---

### 4.2 Learner follows up

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/messages/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "body": "Thank you, that helped!"
}
```

**Expected:** `201 Created`. Message count becomes 3.

---

### 4.3 Blank body rejected — 400

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/messages/
Authorization: {{learner_token}}
Content-Type: application/json

{ "body": "" }
```

**Expected:** `400 Bad Request`.

```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "body": ["This field may not be blank."]
  }
}
```

---

### 4.4 Outsider cannot send — 404

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/messages/
Authorization: {{learner2_token}}
Content-Type: application/json

{ "body": "Sneaky message" }
```

**Expected:** `404 Not Found`.

---

### 4.5 Send-gate: unenrolled learner blocked — 403

In Django admin, set `Enrollment.is_active = False` for the test learner. Then:

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/messages/
Authorization: {{learner_token}}
Content-Type: application/json

{ "body": "Am I still here?" }
```

**Expected:** `403 Forbidden`.

```json
{
  "success": false,
  "message": "You must be actively enrolled to send messages in this course."
}
```

**Verify:** `GET /messaging/conversations/{{conversation_id}}/` still returns `200` — the learner can read historical messages. Reset `is_active = True` after testing.

---

### 4.6 Send-gate: removed instructor blocked — 403

In Django admin, remove the instructor from `course.instructors`. Then:

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/messages/
Authorization: {{instructor_token}}
Content-Type: application/json

{ "body": "I was removed but trying to reply" }
```

**Expected:** `403 Forbidden`.

```json
{
  "success": false,
  "message": "You are no longer an instructor for this course."
}
```

Re-add the instructor after testing.

---

## Group 5: Mark as Read

### 5.1 Learner marks conversation as read — happy path

First, confirm there are unread messages. Run `GET /messaging/conversations/` and note `unread_count > 0` for the conversation (requires the instructor to have sent a message in Group 4.1).

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/read/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`.

```json
{
  "success": true,
  "message": "Marked as read."
}
```

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
```

**Verify:**
```
GET {{base_url}}/messaging/conversations/
Authorization: {{learner_token}}
```
`unread_count` is now `0` for that conversation.

---

### 5.2 Instructor marks conversation as read

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/read/
Authorization: {{instructor_token}}
```

**Expected:** `200 OK`. Only the instructor's `instructor_last_read_at` is updated — the learner's unread count is unaffected.

---

### 5.3 Outsider cannot mark read — 404

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/read/
Authorization: {{learner2_token}}
```

**Expected:** `404 Not Found`.

---

## Group 6: Notification Integration

Sending a message triggers a `message.received` notification for the recipient.

### 6.1 Bell notification created after message sent

1. Have the instructor send a message (Group 4.1).
2. As the learner, check the bell feed:

```
GET {{base_url}}/notifications/
Authorization: {{learner_token}}
```

**Expected:** A notification row with `event_type = message.received`.

```json
{
  "id": 99,
  "event_type": "message.received",
  "title": "New message from Bob Jones",
  "body": "In Python Fundamentals: Great question! The answer is in section 3.2.",
  "data": {
    "conversation_id": 1,
    "course_slug": "python-fundamentals"
  },
  "is_read": false,
  "created_at": "2026-06-16T10:05:00Z"
}
```

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("message.received notification present", () => {
    const results = pm.response.json().data.results;
    const found = results.find(n => n.event_type === "message.received");
    pm.expect(found).to.not.be.undefined;
    pm.expect(found.data).to.have.property("conversation_id");
    pm.expect(found.data).to.have.property("course_slug");
});
```

**Verify:** Terminal shows the email if `EMAIL_BACKEND` is set to console and the learner has `messaging` email preference enabled.

---

### 6.2 Disable email for messaging category

```
PATCH {{base_url}}/notifications/preferences/
Authorization: {{learner_token}}
Content-Type: application/json

{
  "messaging": { "email_enabled": false }
}
```

**Expected:** `200 OK`. `messaging.email_enabled` is `false` in the returned data.

**Verify:** Send another message as instructor. Bell feed notification still created. No email in terminal.

Re-enable:
```json
{ "messaging": { "email_enabled": true } }
```

---

## Group 7: WebSocket Testing

Use [wscat](https://github.com/websockets/wscat) (`npm install -g wscat`) or the Postman WebSocket feature.

### 7.1 Connect and receive unread summary

```bash
wscat -c "ws://localhost:8000/ws/?token={{learner_raw_token}}"
```

> Use the raw JWT token (no `Bearer ` prefix) in the URL.

**Expected immediately on connect (two frames):**
```json
{ "stream": "notifications", "payload": { "type": "unread_count", "count": 2 } }
{ "stream": "messaging",     "payload": { "type": "unread_summary", "conversations": [{ "conversation_id": 1, "unread_count": 1 }] } }
```

> `unread_summary` lists only conversations with unread messages. Empty array if all are read.

---

### 7.2 Send a message via WebSocket

```json
{
  "stream": "messaging",
  "payload": {
    "type": "send_message",
    "conversation_id": 1,
    "body": "Sent over WebSocket!"
  }
}
```

**Sender receives exactly one frame — the immediate acknowledgment:**
```json
{
  "stream": "messaging",
  "payload": {
    "type": "message_sent",
    "message": {
      "id": 4,
      "conversation_id": 1,
      "sender_id": 3,
      "body": "Sent over WebSocket!",
      "is_deleted": false,
      "created_at": "2026-06-16T10:10:00Z"
    }
  }
}
```

The sender does **not** receive a `new_message` frame. `message_sent` is the only delivery to the sender's session.

**If the instructor is connected in a second wscat session, they receive:**
```json
{
  "stream": "messaging",
  "payload": {
    "type": "new_message",
    "conversation_id": 1,
    "message": { "id": 4, ... }
  }
}
```

> `new_message` is pushed only to the recipient's channel group. The sender's group is intentionally excluded — the sender already has the message via `message_sent`.

**Verify:** `GET /messaging/conversations/{{conversation_id}}/` — message appears in REST feed.

---

### 7.3 Mark conversation as read via WebSocket

```json
{
  "stream": "messaging",
  "payload": {
    "type": "mark_read",
    "conversation_id": 1
  }
}
```

**Expected:**
```json
{
  "stream": "messaging",
  "payload": {
    "type": "marked_read",
    "conversation_id": 1
  }
}
```

**Verify:** `GET /messaging/conversations/` via REST — `unread_count` is `0`.

---

### 7.4 Receive a live message from the other party

1. Open two wscat sessions — one as learner, one as instructor.
2. From the instructor session, send:
   ```json
   { "stream": "messaging", "payload": { "type": "send_message", "conversation_id": 1, "body": "Live push test" } }
   ```
3. Instructor session receives `message_sent`.

**Expected on the learner session (no action needed):**
```json
{
  "stream": "messaging",
  "payload": {
    "type": "new_message",
    "conversation_id": 1,
    "message": { "id": 5, "sender_id": 2, "body": "Live push test", ... }
  }
}
```

---

### 7.5 WS error: send to non-existent conversation

```json
{
  "stream": "messaging",
  "payload": {
    "type": "send_message",
    "conversation_id": 99999,
    "body": "Hello"
  }
}
```

**Expected:**
```json
{
  "stream": "messaging",
  "payload": { "type": "error", "detail": "Conversation not found." }
}
```

Connection stays open.

---

### 7.6 WS error: blank body

```json
{
  "stream": "messaging",
  "payload": {
    "type": "send_message",
    "conversation_id": 1,
    "body": "   "
  }
}
```

**Expected:**
```json
{
  "stream": "messaging",
  "payload": { "type": "error", "detail": "body must not be blank." }
}
```

---

### 7.7 WS error: unknown action

```json
{
  "stream": "messaging",
  "payload": { "type": "unknown_action" }
}
```

**Expected:**
```json
{
  "stream": "messaging",
  "payload": { "type": "error", "detail": "Unknown action: unknown_action." }
}
```

---

### 7.8 WS error: unknown stream

```json
{ "stream": "unknown_stream", "payload": {} }
```

**Expected:**
```json
{
  "stream": "error",
  "payload": { "detail": "Unknown stream: unknown_stream." }
}
```

---

### 7.9 Connect without token — auth failure

```bash
wscat -c "ws://localhost:8000/ws/"
```

**Expected:** Connection closed immediately with code `4001`.

---

### 7.10 Connect with invalid/expired token

```bash
wscat -c "ws://localhost:8000/ws/?token=not.a.real.token"
```

**Expected:** Connection closed immediately with code `4001`.

---

## Group 8: Edge Cases

### 8.1 Offline recipient still gets notification

1. Disconnect the learner's wscat session.
2. Send a message as the instructor via REST.
3. Reconnect wscat as the learner.

**Expected on reconnect:** `unread_summary` shows the new unread count. `GET /notifications/` shows the `message.received` notification row.

---

### 8.2 Conversation list ordered by most recent activity

Send messages across multiple conversations. Verify that `GET /messaging/conversations/` returns the conversation with the most recent message first (`ordering = -updated_at`).

---

### 8.3 Admin / partner institution cannot access messaging

```
GET {{base_url}}/messaging/conversations/
Authorization: {{admin_token}}
```

**Expected:** `403 Forbidden`. Only `learner` and `instructor` user types are permitted.

---

## Response Shape Reference

**Start conversation (201):**
```json
{
  "success": true,
  "message": "Conversation started.",
  "data": {
    "id": 1,
    "learner_id": 3,
    "learner_name": "Alice Smith",
    "instructor_id": 2,
    "instructor_name": "Bob Jones",
    "course_title": "Python Fundamentals",
    "course_slug": "python-fundamentals",
    "unread_count": 0,
    "updated_at": "2026-06-16T10:00:00Z",
    "created_at": "2026-06-16T10:00:00Z"
  }
}
```

**Conversation detail (200):**
```json
{
  "success": true,
  "data": {
    "conversation": { "id": 1, "learner_id": 3, "instructor_id": 2, "unread_count": 1, ... },
    "messages": {
      "count": 2,
      "next": null,
      "previous": null,
      "results": [
        { "id": 1, "sender_id": 3, "sender_name": "Alice Smith", "body": "...", "is_own": true, "created_at": "..." }
      ]
    }
  }
}
```

**Send message (201):**
```json
{
  "success": true,
  "message": "Message sent.",
  "data": { "id": 2, "conversation_id": 1, "sender_id": 2, "sender_name": "Bob Jones", "body": "...", "is_own": true, "created_at": "..." }
}
```

**Mark read (200):**
```json
{ "success": true, "message": "Marked as read." }
```

**Error — not found (404):**
```json
{ "success": false, "message": "Conversation not found." }
```

**Error — access denied (403):**
```json
{ "success": false, "message": "You must be actively enrolled in this course to message an instructor." }
```

**Error — validation (400):**
```json
{ "success": false, "message": "Validation failed.", "errors": { "body": ["..."] } }
```

---

## Error Code Summary

| Scenario | Endpoint | Status |
|----------|----------|--------|
| Instructor tries to initiate | `POST conversations/create/` | 403 |
| Unenrolled learner initiates | `POST conversations/create/` | 403 |
| Course not found | `POST conversations/create/` | 404 |
| Instructor not found | `POST conversations/create/` | 404 |
| Blank opener body | `POST conversations/create/` | 400 |
| Unauthenticated | any endpoint | 401 |
| Outsider accesses detail | `GET conversations/<id>/` | 404 |
| Outsider sends message | `POST conversations/<id>/messages/` | 404 |
| Unenrolled learner sends | `POST conversations/<id>/messages/` | 403 |
| Removed instructor sends | `POST conversations/<id>/messages/` | 403 |
| Blank follow-up body | `POST conversations/<id>/messages/` | 400 |
| Outsider marks read | `POST conversations/<id>/read/` | 404 |
| Admin / partner institution | any messaging endpoint | 403 |

---

## Recommended Run Order

```
Setup
  1. Enroll learner in a published course
  2. Note course_id and instructor_id

Group 1: Conversation creation
  1.1  Learner starts conversation    → 201, save conversation_id
  1.2  Same request again             → 200, idempotent
  1.3  Instructor initiates           → 403
  1.4  Unenrolled learner             → 403
  1.5  Bad course id                  → 404
  1.6  Bad instructor id              → 404
  1.7  Blank body                     → 400

Group 2: List
  2.1  Learner lists                  → 200, conversation present
  2.2  Instructor lists               → 200, same conversation
  2.4  Unauthenticated               → 401

Group 3: Detail
  3.1  Learner gets detail            → 200, is_own=true on own msg
  3.2  Instructor gets detail         → 200, is_own=false on learner msg
  3.3  Outsider gets detail           → 404

Group 4: Send messages
  4.1  Instructor replies             → 201, verify count=2
  4.2  Learner follows up             → 201, verify count=3
  4.3  Blank body                     → 400
  4.4  Outsider sends                 → 404
  4.5  Unenrolled learner sends       → 403 (set is_active=False first)
  4.6  Removed instructor sends       → 403 (remove from course.instructors)

Group 5: Mark read
  5.1  Learner marks read             → 200, unread_count=0
  5.2  Instructor marks read          → 200
  5.3  Outsider marks read            → 404

Group 6: Notifications
  6.1  Bell feed has message.received → check after Group 4.1
  6.2  Disable email pref             → no email, bell unaffected

Group 7: WebSocket (requires Daphne + Redis)
  7.1  Connect                        → unread_summary pushed on connect
  7.2  Send via WS                    → message_sent back, new_message to both
  7.3  Mark read via WS               → marked_read back
  7.4  Live push to other party       → two sessions, new_message delivered
  7.5  Send to bad conversation       → error frame
  7.9  Connect without token          → closed 4001

Group 8: Edge cases
  8.1  Offline then reconnect         → unread_summary reflects new messages
  8.3  Admin calls messaging          → 403
```
