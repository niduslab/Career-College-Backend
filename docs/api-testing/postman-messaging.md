# Postman Guide — Messaging System

Manual API testing for the generalized messaging system. A `Conversation` is a **role-neutral 2-party thread** selected by `conversation_type`:

| `conversation_type` | parties | course | who initiates |
|---|---|---|---|
| `learner_instructor` | learner ↔ course instructor | required | learner |
| `co_instructor` | instructor ↔ instructor (same course roster) | required | either instructor |
| `institution_expert` | partner institution ↔ affiliated expert | optional | institution |

The two parties live in a `ConversationParticipant` through-table (each row carries that user's read cursor). The send-gate is dispatched by type in the service and enforced identically on REST + WebSocket. Covers: creation for all three types, message threads, mark-read, real-time WS delivery, and notification integration.

---

## Environment Variables

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `ws_url` | `ws://localhost:8000/ws` | WebSocket base URL |
| `learner_token` | `Bearer eyJ...` | JWT for a learner enrolled in the test course |
| `learner_raw_token` | `eyJ...` | Same JWT without `Bearer ` — used in the WS URL |
| `learner2_token` | `Bearer eyJ...` | JWT for a second learner (not enrolled) — access-denial checks |
| `instructor_token` | `Bearer eyJ...` | JWT for an instructor in the test course |
| `instructor_raw_token` | `eyJ...` | Raw JWT for WS |
| `peer_instructor_token` | `Bearer eyJ...` | JWT for a **second** instructor also on the course (co-instructor tests) |
| `institution_token` | `Bearer eyJ...` | JWT for a partner-institution account |
| `expert_token` | `Bearer eyJ...` | JWT for an instructor who is an **active affiliate** of that institution |
| `admin_token` | `Bearer eyJ...` | JWT for an admin — used for the 403 check |
| `course_id` | `1` | PK of a published course |
| `instructor_id` | `2` | PK of an instructor in `course.instructors.all()` |
| `peer_instructor_id` | `4` | PK of the second course instructor |
| `expert_user_id` | `5` | `User.id` of the affiliated expert |
| `conversation_id` | _(filled during tests)_ | ID of the conversation row |

---

## Prerequisites

1. Django dev server running (ASGI/Daphne for WS — see Group 7).
2. Redis running (Channels WS push + Celery).
3. Celery worker running (notification emails): `celery -A career_college_backend worker -Q celery,notifications -l info`.
4. `EMAIL_BACKEND = django.core.mail.backends.console.EmailBackend` — emails print to the terminal.
5. Data:
   - A published course with a learner actively enrolled (`Enrollment.is_active=True`) and **two** instructors in `course.instructors.all()` (for co-instructor tests). Note `course_id`, `instructor_id`, `peer_instructor_id`.
   - A verified partner institution and an **active affiliated expert** (`InstructorProfile.affiliation_status='active'`, `affiliated_institution=` that institution). Note `expert_user_id`.

---

## Group 1: Learner ↔ Instructor Conversations

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

> `conversation_type` defaults to `learner_instructor` when omitted, so the legacy body still works.

**Expected:** `201 Created`.

```json
{
  "success": true,
  "message": "Conversation started.",
  "data": {
    "id": 1,
    "conversation_type": "learner_instructor",
    "course_id": 1,
    "course_title": "Python Fundamentals",
    "course_slug": "python-fundamentals",
    "participants": [
      { "user_id": 3, "full_name": "Alice Smith", "user_type": "learner", "last_read_at": null },
      { "user_id": 2, "full_name": "Bob Jones", "user_type": "instructor", "last_read_at": null }
    ],
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
pm.test("type + two participants", () => {
    const d = pm.response.json().data;
    pm.expect(d.conversation_type).to.equal("learner_instructor");
    pm.expect(d.participants).to.have.lengthOf(2);
});
pm.test("unread_count is 0 on creation", () => {
    pm.expect(pm.response.json().data.unread_count).to.equal(0);
});
```

---

### 1.2 Same request again — idempotency

Re-send 1.1. **Expected:** `200 OK`, `message: "Conversation already exists."`, same `id`, no extra message.

```javascript
pm.test("200 on duplicate, not 201", () => pm.response.to.have.status(200));
pm.test("same conversation id", () => {
    pm.expect(pm.response.json().data.id).to.equal(pm.environment.get("conversation_id") * 1);
});
```

---

### 1.3 Instructor cannot initiate a learner↔instructor thread — 403

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{instructor_token}}
Content-Type: application/json

{ "course_id": {{course_id}}, "instructor_id": {{instructor_id}}, "body": "Hello learner" }
```

**Expected:** `403 Forbidden` — the role check rejects a non-learner initiator (`"Only a learner can start a conversation with an instructor."`).

---

### 1.4 Unenrolled learner blocked — 403

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner2_token}}
Content-Type: application/json

{ "course_id": {{course_id}}, "instructor_id": {{instructor_id}}, "body": "Can I message?" }
```

**Expected:** `403`, `message: "You must be actively enrolled in this course to message an instructor."`.

---

### 1.5 Non-existent course — 404

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner_token}}
Content-Type: application/json

{ "course_id": 99999, "instructor_id": {{instructor_id}}, "body": "Hello" }
```

**Expected:** `404`, `message: "Course not found."`.

---

### 1.6 Non-existent instructor — 404

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner_token}}
Content-Type: application/json

{ "course_id": {{course_id}}, "instructor_id": 99999, "body": "Hello" }
```

**Expected:** `404`, `message: "Instructor not found."`.

---

### 1.7 Blank body rejected — 400

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner_token}}
Content-Type: application/json

{ "course_id": {{course_id}}, "instructor_id": {{instructor_id}}, "body": "   " }
```

**Expected:** `400`, `errors.body: ["Message body must not be blank."]`.

---

### 1.8 Missing required field for the type — 400

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{learner_token}}
Content-Type: application/json

{ "conversation_type": "learner_instructor", "instructor_id": {{instructor_id}}, "body": "no course" }
```

**Expected:** `400`, `errors.course_id: ["This field is required for this conversation type."]`.

---

## Group 1B: Co-Instructor Conversations

Both parties must be instructors on the same course. Either may initiate.

### 1B.1 Instructor starts a co-instructor thread — happy path

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{instructor_token}}
Content-Type: application/json

{
  "conversation_type": "co_instructor",
  "course_id": {{course_id}},
  "peer_instructor_id": {{peer_instructor_id}},
  "body": "Can you review section 3 before we submit?"
}
```

**Expected:** `201 Created`. `data.conversation_type = "co_instructor"`, participants are the two instructors.

```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.test("co_instructor type", () => pm.expect(pm.response.json().data.conversation_type).to.equal("co_instructor"));
pm.environment.set("coinstructor_conversation_id", pm.response.json().data.id);
```

### 1B.2 Peer instructor replies (WebSocket)

Follow-up messages are sent over the WebSocket `messaging` stream (there is no REST send endpoint — see Group 7). Connect the peer instructor (`ws://localhost:8000/ws/?token=<peer raw JWT>`) and send:

```json
{ "stream": "messaging", "payload": { "type": "send_message", "conversation_id": {{coinstructor_conversation_id}}, "body": "On it — will finish today." } }
```

**Expected:** a `message_sent` ack to the peer; the other instructor (if connected) receives `new_message`.

### 1B.3 Target not on the course roster — 403

Use an instructor **not** in `course.instructors` as `peer_instructor_id`.

**Expected:** `403`, `message: "You are not an instructor for this course."`.

### 1B.4 Removed co-instructor cannot send — 403

Remove the peer instructor from `course.instructors` in admin, then have them POST a message to the thread.

**Expected:** `403`, `message: "You are not an instructor for this course."`. Reading history still returns `200`. Re-add after testing.

---

## Group 1C: Institution ↔ Expert Conversations

Institution-initiated. `course_id` optional (omit for a course-less thread).

### 1C.1 Institution starts a thread with its expert — happy path

```
POST {{base_url}}/messaging/conversations/create/
Authorization: {{institution_token}}
Content-Type: application/json

{
  "conversation_type": "institution_expert",
  "expert_user_id": {{expert_user_id}},
  "body": "Welcome aboard — your first course assignment is coming."
}
```

**Expected:** `201 Created`. `data.conversation_type = "institution_expert"`, `data.course_id = null`, `course_title`/`course_slug` are `null`.

```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.test("courseless institution_expert", () => {
    const d = pm.response.json().data;
    pm.expect(d.conversation_type).to.equal("institution_expert");
    pm.expect(d.course_id).to.equal(null);
});
pm.environment.set("ie_conversation_id", pm.response.json().data.id);
```

### 1C.2 Expert replies (WebSocket)

Connect the expert to the WS `messaging` stream and send:

```json
{ "stream": "messaging", "payload": { "type": "send_message", "conversation_id": {{ie_conversation_id}}, "body": "Thank you! Ready when you are." } }
```

**Expected:** a `message_sent` ack. The institution can send the same way in the same thread.

### 1C.3 Target is not an affiliate — 403

Use an instructor **not** affiliated with the institution as `expert_user_id`.

**Expected:** `403`, `message: "You are no longer an active member of this institution."`.

### 1C.4 Learner/instructor cannot open an institution_expert thread — 403

Send 1C.1 with `{{instructor_token}}`.

**Expected:** `403`, `message: "Only a partner institution can open this conversation."`.

### 1C.5 Deactivated expert cannot send — 403

Set the expert's `InstructorProfile.affiliation_status='removed'`, then have the expert POST a message.

**Expected:** `403`. The **institution** party is never affiliation-gated and can still send. Reset to `active` after testing.

---

## Group 2: Listing Conversations

### 2.1 Learner lists their conversations

```
GET {{base_url}}/messaging/conversations/
Authorization: {{learner_token}}
```

**Expected:** `200 OK`. Each row carries `conversation_type`, `participants`, and the caller's `unread_count`.

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
        "conversation_type": "learner_instructor",
        "course_id": 1,
        "course_title": "Python Fundamentals",
        "course_slug": "python-fundamentals",
        "participants": [
          { "user_id": 3, "full_name": "Alice Smith", "user_type": "learner", "last_read_at": null },
          { "user_id": 2, "full_name": "Bob Jones", "user_type": "instructor", "last_read_at": null }
        ],
        "unread_count": 0,
        "updated_at": "2026-06-16T10:00:00Z",
        "created_at": "2026-06-16T10:00:00Z"
      }
    ]
  }
}
```

```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("conversation appears in inbox", () => {
    const ids = pm.response.json().data.results.map(c => c.id);
    pm.expect(ids).to.include(pm.environment.get("conversation_id") * 1);
});
```

### 2.2 Instructor lists their conversations

`{{instructor_token}}` → `200 OK`. The learner↔instructor and any co-instructor threads they belong to appear.

### 2.3 Pagination

`GET {{base_url}}/messaging/conversations/?page=1&page_size=5` → at most 5 items; `data.next` non-null if more pages.

### 2.4 Unauthenticated — 401

`GET {{base_url}}/messaging/conversations/` (no auth) → `401`.

### 2.5 Unread conversation count (inbox badge)

```
GET {{base_url}}/messaging/conversations/unread-count/
Authorization: {{learner_token}}
```

**Expected:** `200`, `{ "success": true, "data": { "unread_conversations": 1 } }`.

> Counts **distinct conversations** with ≥1 unread message across all types — not total messages. A never-opened thread counts as unread. Equals the length of the WS `unread_summary.conversations` list. Drops to `0` after `POST .../read/` on every unread thread.

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
      "conversation_type": "learner_instructor",
      "course_id": 1,
      "course_title": "Python Fundamentals",
      "course_slug": "python-fundamentals",
      "participants": [
        { "user_id": 3, "full_name": "Alice Smith", "user_type": "learner", "last_read_at": null },
        { "user_id": 2, "full_name": "Bob Jones", "user_type": "instructor", "last_read_at": null }
      ],
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

```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("is_own true for own message", () => {
    pm.expect(pm.response.json().data.messages.results[0].is_own).to.equal(true);
});
pm.test("is_deleted not in payload", () => {
    pm.expect(pm.response.json().data.messages.results[0]).to.not.have.property("is_deleted");
});
```

### 3.2 Instructor gets conversation detail

Same request with `{{instructor_token}}` → `200`. `is_own` is `false` for the learner's opener.

### 3.3 Outsider cannot access — 404

`{{learner2_token}}` → `404`, `message: "Conversation not found."`. Numeric IDs return 404 (not 403) on no-access — existence not leaked.

### 3.4 Paginate messages

`GET {{base_url}}/messaging/conversations/{{conversation_id}}/?page=1&page_size=20` → at most 20, oldest-first.

---

## Group 4: Sending Follow-up Messages (WebSocket only)

**There is no REST send endpoint.** Only the conversation **opener** is persisted over REST (the create call in Groups 1/1B/1C). Every follow-up reply is sent over the WebSocket `messaging` stream — see **Group 7** for the full protocol (`send_message` → `message_sent` ack to sender, `new_message` to the recipient). The send-gate is enforced on that WS path and returns an `error` frame (connection stays open) rather than an HTTP status:

| Send-gate violation (WS `send_message`) | `error.detail` |
|---|---|
| Unenrolled/inactive learner (learner_instructor) | `You must be actively enrolled in this course to message an instructor.` |
| Removed instructor / co-instructor | `You are not an instructor for this course.` |
| Deactivated expert (institution_expert) | `You are no longer an active member of this institution.` |
| Not a participant / unknown conversation | `Conversation not found.` |
| Blank body | `body must not be blank.` |

Reading history is always allowed regardless of send-gate — `GET /messaging/conversations/{{conversation_id}}/` still returns `200`. See Group 7.2 (happy path) and 7.2a (send-gate error).

---

## Group 5: Mark as Read

### 5.1 Learner marks conversation as read

```
POST {{base_url}}/messaging/conversations/{{conversation_id}}/read/
Authorization: {{learner_token}}
```

**Expected:** `200`, `{ "success": true, "message": "Marked as read." }`. Verify `unread_count` is now `0` for that conversation in the list.

### 5.2 Instructor marks conversation as read

`{{instructor_token}}` → `200`. Only the **caller's** participant read cursor is stamped — the other party's unread count is unaffected.

### 5.3 Outsider cannot mark read — 404

`{{learner2_token}}` → `404`.

---

## Group 6: Notification Integration

Sending a message triggers a `message.received` notification for the recipient.

### 6.1 Bell notification created after message sent

Instructor sends a reply over WebSocket (Group 7.2), then as learner: `GET {{base_url}}/notifications/`.

**Expected:** a row with `event_type = message.received`.

```json
{
  "id": 99,
  "event_type": "message.received",
  "title": "New message from Bob Jones",
  "body": "In Python Fundamentals: Great question! The answer is in section 3.2.",
  "data": { "conversation_id": 1, "course_slug": "python-fundamentals" },
  "is_read": false,
  "created_at": "2026-06-16T10:05:00Z"
}
```

> For a **course-less** `institution_expert` thread the body has no `"In <course>:"` prefix — it is just the message preview, and `data.course_slug` is `null`.

```javascript
pm.test("message.received present", () => {
    const found = pm.response.json().data.results.find(n => n.event_type === "message.received");
    pm.expect(found).to.not.be.undefined;
    pm.expect(found.data).to.have.property("conversation_id");
});
```

### 6.2 Disable email for messaging category

```
PATCH {{base_url}}/notifications/preferences/
Authorization: {{learner_token}}
Content-Type: application/json

{ "messaging": { "email_enabled": false } }
```

**Expected:** `200`. Send another message → bell notification still created, no email in terminal. Re-enable with `{ "messaging": { "email_enabled": true } }`.

---

## Group 7: WebSocket Testing

Protocol is unchanged by the generalization — WS message snapshots carry `id, conversation_id, sender_id, body, is_deleted, created_at` for every conversation type. Run with Daphne (`daphne -p 8000 career_college_backend.asgi:application`) + Redis.

Connect: `ws://localhost:8000/ws/?token={{learner_raw_token}}` (raw JWT, no `Bearer `). For two-party live tests open a second tab with `{{instructor_raw_token}}`.

### 7.1 Connect and receive unread summary

On connect, two frames arrive automatically:
```json
{ "stream": "notifications", "payload": { "type": "unread_count", "count": 2 } }
{ "stream": "messaging",     "payload": { "type": "unread_summary", "conversations": [{ "conversation_id": 1, "unread_count": 1 }], "unread_conversations": 1 } }
```
`unread_conversations` = length of the list (same value the REST `unread-count/` returns). Spans all conversation types the user belongs to.

### 7.2 Send a message via WebSocket

```json
{ "stream": "messaging", "payload": { "type": "send_message", "conversation_id": 1, "body": "Sent over WebSocket!" } }
```

Sender receives exactly one `message_sent` ack (not `new_message`):
```json
{ "stream": "messaging", "payload": { "type": "message_sent", "message": { "id": 4, "conversation_id": 1, "sender_id": 3, "body": "Sent over WebSocket!", "is_deleted": false, "created_at": "..." } } }
```
If the other party is connected, they receive a `new_message` frame with the same snapshot. The sender is intentionally excluded from the recipient broadcast to avoid duplicate delivery.

### 7.2a Send-gate error via WebSocket

With the learner's enrollment inactive, sending yields (connection stays open):
```json
{ "stream": "messaging", "payload": { "type": "error", "detail": "You must be actively enrolled in this course to message an instructor." } }
```
Reset `is_active=True` after.

### 7.3 Mark read via WebSocket

```json
{ "stream": "messaging", "payload": { "type": "mark_read", "conversation_id": 1 } }
```
→ `{ "stream": "messaging", "payload": { "type": "marked_read", "conversation_id": 1 } }`. Verify `unread_count` is `0` via REST.

### 7.4 Live push to the other party

Two tabs (learner + instructor). Instructor sends → instructor gets `message_sent` only; learner gets `new_message` automatically. Works identically for a co-instructor or institution↔expert thread — connect the two relevant parties.

### 7.5–7.8 WS error frames

- Unknown conversation id → `{ "type": "error", "detail": "Conversation not found." }`
- Blank body → `{ "type": "error", "detail": "body must not be blank." }`
- Unknown action → `{ "type": "error", "detail": "Unknown action: <x>." }`
- Unknown stream → `{ "stream": "error", "payload": { "detail": "Unknown stream: <x>." } }`

### 7.9 / 7.10 Auth failure

Connect with no token or an invalid token → connection rejected immediately, close code `4001`.

---

## Group 8: Edge Cases & Access

### 8.1 Offline recipient still gets notification

Disconnect learner WS → instructor sends via REST → reconnect learner. `unread_summary` reflects the new unread; `GET /notifications/` shows the `message.received` row.

### 8.2 Conversation list ordered by most recent activity

Send messages across multiple threads → `GET /messaging/conversations/` returns the most-recently-active first (`ordering = -updated_at`).

### 8.3 Access by user type

| Caller | `GET /messaging/conversations/` |
|---|---|
| Learner | 200 |
| Instructor | 200 |
| Partner institution | 200 (needed for institution↔expert threads) |
| Admin | **403** |

```
GET {{base_url}}/messaging/conversations/
Authorization: {{admin_token}}
```
**Expected:** `403 Forbidden`. Admins are excluded from messaging.

---

## Response Shape Reference

**Conversation object (create 201 / list / detail):**
```json
{
  "id": 1,
  "conversation_type": "learner_instructor",
  "course_id": 1,
  "course_title": "Python Fundamentals",
  "course_slug": "python-fundamentals",
  "participants": [
    { "user_id": 3, "full_name": "Alice Smith", "user_type": "learner", "last_read_at": null },
    { "user_id": 2, "full_name": "Bob Jones", "user_type": "instructor", "last_read_at": null }
  ],
  "unread_count": 0,
  "updated_at": "2026-06-16T10:00:00Z",
  "created_at": "2026-06-16T10:00:00Z"
}
```
> For `institution_expert` with no course: `course_id`, `course_title`, `course_slug` are all `null`.

**Send message — WebSocket only.** The sender's `message_sent` ack frame carries:
```json
{ "stream": "messaging", "payload": { "type": "message_sent",
  "message": { "id": 2, "conversation_id": 1, "sender_id": 2, "body": "...", "is_deleted": false, "created_at": "..." } } }
```
(There is no REST `POST .../messages/` endpoint.)

**Unread conversation count (200):** `{ "success": true, "data": { "unread_conversations": 1 } }`

**Mark read (200):** `{ "success": true, "message": "Marked as read." }`

**Not found (404):** `{ "success": false, "message": "Conversation not found." }`

**Validation (400):** `{ "success": false, "message": "Validation failed.", "errors": { "body": ["..."] } }`

---

## Error Code Summary

| Scenario | Endpoint | Status |
|----------|----------|--------|
| Instructor initiates learner↔instructor thread | `POST conversations/create/` | 403 |
| Unenrolled learner initiates | `POST conversations/create/` | 403 |
| Co-instructor target not on roster | `POST conversations/create/` | 403 |
| Institution↔expert target not an affiliate | `POST conversations/create/` | 403 |
| Non-institution opens institution↔expert | `POST conversations/create/` | 403 |
| Course not found | `POST conversations/create/` | 404 |
| Instructor / expert not found | `POST conversations/create/` | 404 |
| Blank opener body | `POST conversations/create/` | 400 |
| Missing required id for the type | `POST conversations/create/` | 400 |
| Unauthenticated | any endpoint | 401 |
| Outsider accesses detail | `GET conversations/<id>/` | 404 |
| Outsider marks read | `POST conversations/<id>/read/` | 404 |
| Admin | any messaging endpoint | 403 |

Follow-up send-gate violations happen on the **WebSocket** `send_message` action and surface as `error` frames (connection stays open), not HTTP statuses — see Group 4 and Group 7.2a.

---

## Recommended Run Order

```
Setup
  Enroll a learner + add TWO instructors to a published course.
  Create a verified institution + an active affiliated expert.

Group 1  Learner↔Instructor
  1.1 start → 201 (save conversation_id)   1.2 duplicate → 200
  1.3 instructor initiates → 403           1.4 unenrolled → 403
  1.5 bad course → 404                     1.6 bad instructor → 404
  1.7 blank body → 400                     1.8 missing course_id → 400

Group 1B Co-Instructor
  1B.1 instructor starts → 201             1B.2 peer replies → 201
  1B.3 target off roster → 403             1B.4 removed co-instructor sends → 403

Group 1C Institution↔Expert
  1C.1 institution starts (courseless) → 201   1C.2 expert replies → 201
  1C.3 non-affiliate target → 403          1C.4 non-institution opens → 403
  1C.5 deactivated expert sends → 403

Group 2  List: 2.1/2.2 → 200, 2.4 → 401, 2.5 unread count
Group 3  Detail: 3.1 is_own=true, 3.2 is_own=false, 3.3 outsider → 404
Group 4  Follow-up sends are WebSocket-only → see Group 7 (send-gate → error frame)
Group 5  Mark read: 5.1 → unread 0, 5.2 own cursor only, 5.3 outsider → 404
Group 6  Notifications: 6.1 bell has message.received, 6.2 disable email
Group 7  WebSocket (Daphne+Redis): 7.1 connect, 7.2 send, 7.3 read, 7.4 live push, 7.5+ errors, 7.9 no token → 4001
Group 8  Edge: 8.1 offline reconnect, 8.2 ordering, 8.3 admin → 403 / institution → 200
```
