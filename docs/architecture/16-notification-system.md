# 16 — Notification System

> Status: **Implemented**
> Scope: Real-time WebSocket notifications + email notifications across all platform events

---

## Table of Contents

1. [What This System Does (Plain English)](#1-what-this-system-does-plain-english)
2. [How Notifications Reach You](#2-how-notifications-reach-you)
3. [All 21 Notification Events](#3-all-21-notification-events)
4. [Architecture Overview (Technical)](#4-architecture-overview-technical)
5. [App Structure](#5-app-structure)
6. [WebSocket — Real-Time Push](#6-websocket--real-time-push)
7. [Email Delivery](#7-email-delivery)
8. [Where Each Notification Is Triggered](#8-where-each-notification-is-triggered)
9. [User Preferences](#9-user-preferences)
10. [Scalability](#10-scalability)
11. [Security](#11-security)
12. [Edge Cases & Failure Handling](#12-edge-cases--failure-handling)
13. [Impact on Existing Features](#13-impact-on-existing-features)
14. [REST API Reference](#14-rest-api-reference)

---

## 1. What This System Does (Plain English)

Before this system existed, the platform could only send emails for three things — a one-time login code (OTP), a course completion certificate, and a co-instructor invite. These were scattered across different files and had no single place to manage them.

This system adds **three things**:

### 1. A bell icon feed (in-app notifications)
Every event the platform cares about — a learner enrolling, an assignment being graded, a course being approved — creates a small notification record in the database. When you open the bell icon, you see all your recent notifications with unread counts, just like on LinkedIn or YouTube.

### 2. Real-time pop-ups while you're using the site
If you have the website open, notifications appear instantly without you needing to refresh. This works through a technology called WebSockets — a permanent two-way connection between your browser and the server, like a phone call that stays open rather than sending letters back and forth.

### 3. Email for important events
For events that matter even when you're not on the site — your course got approved, your enrollment was confirmed, your identity verification was rejected — the system also sends an email. Users can turn email off per category in their preferences.

### The key design principle: one dispatcher, not many scattered functions

All 21 event types go through a single function called `dispatch()`. When anything happens on the platform, the code says:

```python
dispatch(event_type, recipients, context)
```

The dispatcher then handles saving to the database, pushing to WebSocket, and sending the email. The code that triggers the event does not need to know how delivery works.

---

## 2. How Notifications Reach You

Here is the journey of a single notification, step by step:

```
Something happens on the platform
        │
        ▼
dispatch() is called with the event type, recipient list, and details
        │
        ├──► 1. Save a Notification row to the database
        │         (this is the in-app bell feed record)
        │
        ├──► 2. Push to WebSocket (instant, if you're online)
        │         Your browser receives the notification in < 1 second
        │         If you're offline, Redis discards the push after 60 seconds
        │         — the bell feed (step 1) is your safety net
        │
        └──► 3. Queue an email via Celery (background job)
                  The email worker picks it up and sends via SMTP
                  If SMTP fails, it retries up to 3 times with increasing delays
                  If you turned off email for this category, the worker skips it
```

**What happens if you're offline when a notification fires?**
The WebSocket push is dropped (no one is listening), but the database row was already saved. When you next open the site, the bell icon shows the correct unread count and the `GET /api/v1/notifications/` endpoint returns everything you missed.

**What happens if the notification system itself fails?**
The `dispatch()` function catches all exceptions and logs them. It never propagates errors back to the caller. If the notification fails, the main business action (enrolling a user, grading an assignment) still succeeds. Notifications are a side effect, not the primary operation.

---

## 3. All 21 Notification Events

### Learner events (things that happen to you as a student)

| What happened | When it fires | Bell feed | Email |
|---|---|---|---|
| You enrolled in a course | After successful enrollment | ✓ | ✓ |
| You finished a lecture | When your video reaches 100% | ✓ | — |
| You submitted a quiz | After quiz answers are saved | ✓ | — |
| Your assignment was graded | After AI grader finishes (passed or failed) | ✓ | — |
| Your coding exercise was evaluated | After Docker runner finishes | ✓ | — |
| You completed the whole course | When progress hits 100% (certificate issued) | ✓ | ✓ |

### Instructor / course creator events (things that happen on your courses)

| What happened | When it fires | Bell feed | Email |
|---|---|---|---|
| Your course was submitted for admin review | After you click Submit for Review | ✓ | ✓ (admins get this) |
| Your course was approved and published | After admin approves | ✓ | ✓ |
| Your course was rejected | After admin rejects | ✓ | ✓ |
| Your course was archived | After archive action | ✓ | — |
| A video finished processing | After FFmpeg transcoding completes | ✓ | — |
| A video failed to process | After all retry attempts fail | ✓ | — |
| You were invited as a co-instructor | After course owner sends invite | ✓ | ✓ |
| Someone accepted your instructor invite | After invitee accepts | ✓ | ✓ |
| Someone declined your instructor invite | After invitee declines | ✓ | ✓ |
| A learner left a new review on your course | After review is created | ✓ | — |
| A new learner enrolled in your course | After enrollment | ✓ | — |

### Identity verification events (instructor verification flow)

| What happened | When it fires | Bell feed | Email |
|---|---|---|---|
| An instructor submitted a verification request | After submission | ✓ | ✓ (admins get this) |
| Your verification was approved | After admin approves | ✓ | ✓ |
| Your verification was rejected | After admin rejects | ✓ | ✓ |
| Admin requested more information | After admin sets action_required | ✓ | ✓ |

---

## 4. Architecture Overview (Technical)

### Two new Django apps

The system is split into two apps deliberately:

- **`realtime/`** — owns the WebSocket infrastructure only. No domain logic. No knowledge of what a "notification" is.
- **`notifications/`** — owns the domain logic: what events exist, what messages they produce, email templates, the bell feed REST API, user preferences.

They are separate because a future **`messaging/`** feature (instructor–learner chat) will also need the WebSocket infrastructure from `realtime/` — but it must not depend on `notifications/`. Keeping them separate means `messaging` can reuse `realtime` without creating circular imports.

### High-level flow

```
Business Logic Layer                    Notification System
─────────────────────────────────────  ──────────────────────────────────────────
enrollment_service.py
review_service.py          dispatch(event, recipients, context)
courses/tasks.py       ──► notifications/services/dispatcher.py
id_verification/views              │
status_views.py                    ├──► DB write (Notification row)
signals.py                         │        └──► async_to_sync(channel_layer.group_send)
                                   │              → PlatformConsumer (WebSocket)
                                   │
                                   └──► send_notification_email_task.delay(pk)
                                             → Celery worker → SMTP
```

### The `dispatch()` function

```python
# The only function callers need to know
from notifications.services.dispatcher import dispatch
from notifications.models import NotificationEventType

dispatch(
    NotificationEventType.ENROLLMENT_CREATED,  # what happened
    recipients=[enrollment.user],              # who gets it
    context={                                  # details for the message
        'course_title': course.title,
        'course_slug': course.slug,
    },
)
```

`dispatch()` never raises exceptions. Failures are logged at WARNING/ERROR level and swallowed so the caller's business logic is never affected.

### Transaction safety rule

When `dispatch()` is called from inside a view or service that runs inside a database transaction, it must be wrapped in `transaction.on_commit()`:

```python
# Inside a view (inside an atomic block):
transaction.on_commit(lambda: dispatch(NotificationEventType.INVITE_SENT, ...))
```

This prevents notifications from firing if the outer transaction rolls back (which would mean the thing you're notifying about never actually happened).

When `dispatch()` is called from inside a Celery task body, it is called directly — Celery tasks run outside HTTP request transactions.

---

## 5. App Structure

```
realtime/                              ← WebSocket infrastructure only
├── __init__.py
├── apps.py
├── middleware.py                      # Reads ?token= from the WS URL, validates JWT
├── routing.py                         # ws/ URL pattern
├── consumers.py                       # PlatformConsumer — routes messages to stream handlers
└── streams/
    ├── base.py                        # BaseStreamHandler parent class
    ├── notifications_stream.py        # Handles the "notifications" stream
    └── messaging_stream.py           # Stub for future messaging feature

notifications/
├── __init__.py
├── apps.py
├── models.py                          # Notification, NotificationPreference, event type enums
├── admin.py                           # Django admin registration
├── urls.py                            # REST API URL patterns
├── tasks.py                           # Celery: send_notification_email_task, purge_old_notifications_task
├── email_utils.py                     # Maps event types to HTML email templates
├── serializers.py                     # API response shape
├── views.py                           # REST views (list, mark-read, unread-count, preferences)
├── services/
│   ├── dispatcher.py                  # dispatch() — the single public API
│   ├── builders.py                    # Generates title/body/data per event type
│   └── preference_service.py         # get_email_preference(user, event_type) → bool
└── templates/notifications/emails/
    ├── base_notification.html         # Base layout for all emails
    ├── base_notification.txt          # Plain-text fallback
    ├── enrollment_created.html
    ├── assignment_graded.html
    ├── course_completed.html
    ├── course_submitted.html
    ├── course_approved.html
    ├── course_rejected.html
    ├── invite_sent.html
    ├── invite_accepted.html
    ├── invite_declined.html
    ├── verification_submitted.html
    ├── verification_approved.html
    ├── verification_rejected.html
    ├── verification_action_required.html
    └── video_transcoding_failed.html
```

### Database models

#### `Notification`

One row per recipient per event. The bell feed queries this table.

| Column | Type | Purpose |
|---|---|---|
| `recipient` | FK to User | Who this notification belongs to |
| `event_type` | string | Which of the 21 events this is |
| `title` | string | Short heading (e.g. "Assignment graded") |
| `body` | text | One-sentence description |
| `data` | JSON | Deep-link payload for the frontend (course_slug, submission_id, etc.) |
| `is_read` | bool | Whether the user has seen it |
| `read_at` | datetime | When they marked it read |
| `deduplication_key` | string (unique) | Prevents duplicate rows from Celery task re-delivery |
| `created_at` | datetime | When it was created |

**Why `data` is a JSON field:** Each event needs different context (enrollment needs a course slug, assignment grading needs a submission ID). A single flexible JSON field is cleaner than adding a nullable column for every possible piece of data.

#### `NotificationPreference`

One row per user per category. Created lazily on first access (default: all enabled).

| Column | Type | Purpose |
|---|---|---|
| `user` | FK to User | Which user |
| `category` | string | One of: course_activity, assessments, course_management, collaboration, verification |
| `email_enabled` | bool | Whether to send email for this category |
| `push_enabled` | bool | Whether in-app push is enabled (reserved for future mobile push) |

---

## 6. WebSocket — Real-Time Push

### Why a single multiplexed connection

The platform uses **one WebSocket connection per browser tab**, shared by all features. A stream envelope wraps every message:

```json
{ "stream": "notifications", "payload": { ... } }
{ "stream": "messaging",     "payload": { ... } }
```

**Why not one connection per feature?**
- One TLS handshake and one JWT validation, not multiple.
- Simpler reconnection logic on the frontend — one place to retry.
- When the messaging feature is added, it reuses the same connection without any changes to how notifications work.

### Authentication

WebSocket connections cannot carry HTTP headers (unlike regular API calls). So the access token is passed as a URL query parameter:

```
ws://localhost:8000/ws/?token=<your_access_token>
```

The `JWTAuthMiddleware` in `realtime/middleware.py` reads this token, validates it, and attaches the user to the connection. Connections without a valid token are immediately closed with code `4001`.

### Stream handlers

Inside `PlatformConsumer`, each stream (notifications, messaging) has its own handler class. The consumer reads the `stream` field of every inbound message and calls the right handler's `on_receive()` method.

```
Client sends: {"stream": "notifications", "payload": {"type": "mark_all_read"}}
                    │
                    ▼
         PlatformConsumer.receive()
                    │
         stream = "notifications"
                    │
                    ▼
         NotificationStreamHandler.on_receive()
         → marks all rows is_read=True for this user
         → sends back: {"stream": "notifications", "payload": {"type": "unread_count", "count": 0}}
```

### Channel groups

Each user is subscribed to a private Redis channel group: `notifications_user_{user_id}`.

When the dispatcher pushes a notification, it calls:
```python
channel_layer.group_send('notifications_user_42', {'type': 'notification.push', ...})
```

If user 42 has an open WebSocket connection, the `PlatformConsumer` receives this event and forwards it to `NotificationStreamHandler.handle_notification_push()`, which sends the notification down to the browser wrapped in the stream envelope.

If user 42 is offline, the Redis message expires after 60 seconds with no effect.

### On-connect behaviour

The moment a user's WebSocket connects and authenticates, the notification stream handler:
1. Subscribes to `notifications_user_{id}` channel group.
2. Immediately sends the current unread count so the bell icon is correct without a REST call.

```json
{ "stream": "notifications", "payload": { "type": "unread_count", "count": 7 } }
```

### Wire protocol — complete reference

**Inbound (client → server):**
```json
{ "stream": "notifications", "payload": { "type": "mark_read",     "ids": [1, 2, 3] } }
{ "stream": "notifications", "payload": { "type": "mark_all_read" } }
{ "stream": "messaging",     "payload": { "type": "anything" }  }
```

**Outbound (server → client):**
```json
{
  "stream": "notifications",
  "payload": {
    "type": "notification",
    "id": 42,
    "event_type": "enrollment.created",
    "title": "Enrolled in Python Basics",
    "body": "You are now enrolled in \"Python Basics\". Start learning!",
    "data": { "course_slug": "python-basics" },
    "is_read": false,
    "created_at": "2026-06-14T10:22:00Z"
  }
}

{ "stream": "notifications", "payload": { "type": "unread_count", "count": 7 } }

{ "stream": "error",         "payload": { "detail": "Unknown stream: foo." } }
```

### Messaging stream (stub)

`MessagingStreamHandler` is registered in `PlatformConsumer` but all its methods are empty no-ops. The wire protocol for the messaging stream is stable from day one. When the messaging feature is built:
- Implement `on_connect`, `on_disconnect`, `on_receive` in `messaging_stream.py`.
- Add any new channel-layer event types to `_CHANNEL_EVENT_DISPATCH` in `consumers.py`.
- No changes needed to `PlatformConsumer` itself.

### Adding a completely new stream in the future

```python
# 1. Create realtime/streams/my_stream.py
class MyStreamHandler(BaseStreamHandler):
    stream_name = 'my_feature'
    async def on_connect(self, user): ...
    async def on_receive(self, user, data): ...

# 2. Register in consumers.py
_STREAM_HANDLER_CLASSES = {
    ...
    MyStreamHandler.stream_name: MyStreamHandler,
}

# 3. Add channel-layer events if needed
_CHANNEL_EVENT_DISPATCH = {
    ...
    'my_feature.push': ('my_feature', 'handle_push'),
}

# 4. Add the corresponding method on PlatformConsumer
async def my_feature_push(self, event):
    stream_name, method = _CHANNEL_EVENT_DISPATCH['my_feature.push']
    await getattr(self._handlers[stream_name], method)(event)
```

That is the entire addition. Nothing else changes.

---

## 7. Email Delivery

### How it works

The dispatcher does not send email directly. It enqueues a Celery background task:

```
dispatch() → transaction.on_commit() → send_notification_email_task.delay(notification_pk)
```

The Celery worker then:
1. Loads the `Notification` row.
2. Checks if the recipient still exists and is active.
3. Checks the user's email preference for this event category.
4. If enabled: renders the HTML template and sends via Django's email backend (SMTP).
5. If the SMTP server is unavailable: retries up to 3 times with exponential backoff (delays: 1s, 2s, 4s, max 5 minutes).

### Why background, not direct?

Email delivery can take hundreds of milliseconds and fail due to transient SMTP issues. Doing it synchronously inside a view would make the API slow and fragile. Celery handles retries automatically.

### Dedicated queue

Email tasks run in their own Celery queue called `notifications`. This prevents a backlog of slow assignment grading tasks from delaying email delivery.

Start the notifications worker:
```bash
celery -A career_college_backend worker -Q notifications -c 4 -l info
```

### Email templates

All email templates extend `base_notification.html` which provides the Career College header, footer, and branding. Each event has its own template file under `notifications/templates/notifications/emails/`.

### Events that send email vs. WS-only

| Email + WS | WS only (no email) |
|---|---|
| enrollment.created | lecture.completed |
| course.completed | |
| course.submitted_for_review | video.transcoding_completed |
| course.approved | video.transcoding_failed |
| course.rejected | review.received |
| invite.sent | learner.enrolled |
| invite.accepted | |
| invite.declined | |
| verification.submitted | |
| verification.approved | |
| verification.rejected | |
| verification.action_required | |

The WS-only events are things a user would notice live (a lecture completing) where an email would feel spammy.

---

## 8. Where Each Notification Is Triggered

Every `dispatch()` call is placed in the service or task layer, not in views or serializers.

| Event | File | Function |
|---|---|---|
| `enrollment.created` | `courses/services/enrollment_service.py` | `enroll_learner()` |
| `learner.enrolled` | `courses/services/enrollment_service.py` | `enroll_learner()` |
| `course.completed` | `courses/services/enrollment_service.py` | `_issue_certificate_and_notify()` |
| `lecture.completed` | `courses/signals.py` | `recalculate_enrollment_progress_on_watch_update` |
| `video.transcoding_completed` | `courses/tasks.py` | `transcode_video_asset_task` |
| `video.transcoding_failed` | `courses/tasks.py` | `transcode_video_asset_task` |
| `course.submitted_for_review` | `courses/all_views/status_views.py` | `CourseSubmitForReviewView` |
| `course.approved` | `courses/all_views/status_views.py` | `CourseAdminReviewView` |
| `course.rejected` | `courses/all_views/status_views.py` | `CourseAdminReviewView` |
| `invite.sent` | `courses/services/invite_service.py` | `create_instructor_invite()` |
| `invite.accepted` | `courses/services/invite_service.py` | `accept_instructor_invite()` |
| `invite.declined` | `courses/services/invite_service.py` | `decline_instructor_invite()` |
| `review.received` | `courses/services/review_service.py` | `create_or_update_review()` |
| `verification.submitted` | `id_verification/all_views/instructor_views.py` | `VerificationSubmitView` |
| `verification.approved` | `id_verification/all_views/admin_views.py` | `AdminVerificationReviewView` |
| `verification.rejected` | `id_verification/all_views/admin_views.py` | `AdminVerificationReviewView` |
| `verification.action_required` | `id_verification/all_views/admin_views.py` | `AdminVerificationReviewView` |

### The on_commit pattern

All view/service triggers wrap `dispatch()` in `transaction.on_commit()` because they run inside Django's request-transaction cycle. If the outer transaction rolls back (e.g. a database constraint fails), the notification is never sent.

```python
# Pattern for views and services (inside an atomic block):
_ctx = {'course_title': course.title, 'course_slug': course.slug}
transaction.on_commit(lambda: dispatch(NotificationEventType.COURSE_APPROVED, instructors, context=_ctx))
```

### Direct dispatch in Celery tasks

Inside Celery task bodies, `dispatch()` is called directly (no `transaction.on_commit()`). Tasks run outside HTTP request transactions. The email sub-task enqueued by `dispatch()` still uses `transaction.on_commit()` internally for its own safety.

### Deduplication for Celery events

For events fired from `acks_late=True` Celery tasks (assignment grading, coding grading, video transcoding), a `deduplication_key` is set on the notification row. If the task is redelivered after a worker crash, the second `dispatch()` call finds the existing row and skips the push + email.

Deduplication key format: `{event_type}:{recipient_id}:{object_id}`.

---

## 9. User Preferences

### Categories

Events are grouped into five categories. Users opt in/out per category, not per individual event:

| Category | Events covered |
|---|---|
| `course_activity` | Enrollment created, lecture completed, course completed, review received, learner enrolled |
| `assessments` | Quiz submitted, assignment graded, coding graded |
| `course_management` | Course submitted/approved/rejected/archived, video ready/failed |
| `collaboration` | Invite sent/accepted/declined |
| `verification` | All four verification state changes |

### Default: all email on

Preference rows are created lazily — a user with no rows gets the same result as a user with all rows set to `email_enabled=True`. The `get_or_create` with `defaults={'email_enabled': True, 'push_enabled': True}` handles this.

### API

```
GET  /api/v1/notifications/preferences/
→ 200 { success: true, data: [{category, email_enabled, push_enabled}, ...] }

PATCH /api/v1/notifications/preferences/
Body: { "assessments": { "email_enabled": false } }
→ 200 { success: true, message: "Preferences updated.", data: [...] }
```

---

## 10. Scalability

### Fan-out at dispatch time

For each event, `dispatch()` creates one `Notification` row and one channel layer group_send call per recipient. For most events this is 1–5 recipients. The highest fan-out is `COURSE_SUBMITTED` and `VERIFICATION_SUBMITTED` (notifying all admin users — typically fewer than 20).

**Future concern:** If a course ever has thousands of enrolled learners and an announcement-type event is added, a direct fan-out would be expensive. The solution at that scale is a `NotificationBroadcast` model (one row per event) with a background task that fans out to recipients in batches. The `dispatch()` API signature does not need to change for this upgrade.

### WebSocket connections

Django Channels with Redis channel layer handles tens of thousands of concurrent connections per server process. Multiple ASGI workers share the Redis channel layer. Horizontal scaling works out of the box.

### Database indexes

The `Notification` table has a composite index on `(recipient, is_read, created_at)` which covers the bell feed query:

```sql
SELECT * FROM notifications_notification
WHERE recipient_id = %s AND is_read = false
ORDER BY created_at DESC
LIMIT 20;
```

### Notification retention

A daily Celery Beat task (`purge_old_notifications_task`) deletes `Notification` rows that are both read and older than 90 days. Unread notifications are never purged automatically.

---

## 11. Security

### WebSocket authentication

- Connections without a valid `?token=` are closed with code `4001` before `connect()` completes.
- `AllowedHostsOriginValidator` in `asgi.py` blocks WS connections from origins not in `ALLOWED_HOSTS`.
- JWT tokens have a 12-hour lifetime. An established WS connection does not re-validate mid-session — client-side reconnection with a fresh token handles expiry naturally.
- In production, all traffic must be over WSS (TLS termination at the reverse proxy).

### REST endpoint security

- `GET /api/v1/notifications/` filters by `recipient=request.user` in the queryset — not just by permission class. Even if a bug exposed the view to the wrong user, they would receive an empty list.
- `POST /api/v1/notifications/mark-read/` applies `filter(recipient=request.user)` before the update — passing someone else's notification IDs is silently ignored, not an error.

### No sensitive data in notification payloads

The `body` text and `data` JSON must never include:
- OTP codes or login tokens
- JWT access/refresh tokens
- Password reset tokens
- Course `solution_code` or hidden test cases

The `data` field contains only IDs and slugs that are needed for frontend routing.

### Admin fan-out safety

Dispatching to "all admins" uses `User.objects.filter(user_type='admin', is_deleted=False, is_active=True)`. This query runs inside the `on_commit` callback, not the main request, so it does not hold a DB connection during the HTTP response.

---

## 12. Edge Cases & Failure Handling

| Scenario | How it is handled |
|---|---|
| `dispatch()` raises an exception | Caught and logged. Never propagates to caller. Business logic succeeds. |
| WS push to offline user | `group_send` is a no-op. Redis drops the message after 60s. Bell feed (DB) is the safety net. |
| Email task fails after 3 retries | Notification row exists in the bell feed. User sees it there. Email was not delivered. |
| Celery task re-delivered (acks_late) | `deduplication_key` on the Notification row prevents a second row being created. |
| `dispatch()` inside a rolled-back transaction | Wrapped in `transaction.on_commit()` — it never fires if the outer transaction rolls back. |
| User deleted between dispatch and email delivery | `send_notification_email_task` checks `is_deleted` and `is_active` before sending. |
| User opts out of email | `get_email_preference()` returns False. Email task returns early. |
| No admins in the system | `dispatch(COURSE_SUBMITTED, [], ...)` — empty recipients list is guarded at the top of `dispatch()`. |
| Lecture completion signal fires multiple times | The signal checks `is_completed` transition from False→True using `_previous_is_completed` cached in `pre_save`. Only fires once per actual completion event. |

---

## 13. Impact on Existing Features

### What was removed

| Removed | Replaced by |
|---|---|
| `send_certificate_email_task` in `courses/tasks.py` | `dispatch(COURSE_COMPLETED, ...)` in `_issue_certificate_and_notify()` |
| `send_instructor_invite_email_task` in `courses/tasks.py` | `dispatch(INVITE_SENT, ...)` in `create_instructor_invite()` |
| `send_certificate_email()` in `courses/email_utils.py` | Notification email template `course_completed.html` |
| `send_instructor_invite_email()` in `courses/email_utils.py` | Notification email template `invite_sent.html` |

### What was not touched

- `send_otp_email()` in `authentication/utils/email_utils.py` — OTP is a synchronous authentication primitive, not a notification. It stays as-is.
- All existing API endpoints — the notification system is purely additive. No existing responses changed.
- Certificate issuance logic — `issue_certificate()` still runs. The only change is that the certificate email now goes through the unified dispatcher instead of its own task.

### Frontend integration checklist

The frontend is not required to change any existing API call. To add notification UI:

1. Connect: `new WebSocket('wss://host/ws/?token=' + accessToken)`
2. On message: parse `envelope.stream`, dispatch `envelope.payload` to the right handler.
3. On `stream=notifications, type=unread_count`: update bell badge number.
4. On `stream=notifications, type=notification`: prepend to bell dropdown, increment badge.
5. On reconnect: call `GET /api/v1/notifications/` to backfill missed notifications.
6. Mark read via WS: `ws.send(JSON.stringify({stream:'notifications', payload:{type:'mark_all_read'}}))`
7. Or mark read via REST: `POST /api/v1/notifications/mark-read/` with `{"all": true}`

---

## 14. REST API Reference

All endpoints require authentication (`Bearer` token) and email verification. All responses follow the `{success, data, message}` envelope.

### `GET /api/v1/notifications/`

Returns your notifications, newest first, paginated.

**Query parameters:**
- `?is_read=false` — filter to unread only
- `?event_type=enrollment.created` — filter by event type
- `?page_size=20` — up to 100 per page

**Response:**
```json
{
  "success": true,
  "data": {
    "count": 42,
    "next": "...",
    "previous": null,
    "results": [
      {
        "id": 99,
        "event_type": "enrollment.created",
        "title": "Enrolled in Python Basics",
        "body": "You are now enrolled in \"Python Basics\". Start learning!",
        "data": { "course_slug": "python-basics" },
        "is_read": false,
        "read_at": null,
        "created_at": "2026-06-14T10:22:00Z"
      }
    ]
  }
}
```

### `POST /api/v1/notifications/mark-read/`

Mark specific notifications or all notifications as read.

**Body (mark specific):**
```json
{ "ids": [1, 2, 3] }
```

**Body (mark all):**
```json
{ "all": true }
```

**Response:**
```json
{ "success": true, "message": "Marked as read." }
```

### `GET /api/v1/notifications/unread-count/`

Quick unread count for the bell icon badge.

**Response:**
```json
{ "success": true, "data": { "count": 7 } }
```

### `GET /api/v1/notifications/preferences/`

Get all preference categories and their current settings.

**Response:**
```json
{
  "success": true,
  "data": [
    { "category": "course_activity",   "email_enabled": true,  "push_enabled": true },
    { "category": "assessments",       "email_enabled": true,  "push_enabled": true },
    { "category": "course_management", "email_enabled": true,  "push_enabled": true },
    { "category": "collaboration",     "email_enabled": true,  "push_enabled": true },
    { "category": "verification",      "email_enabled": true,  "push_enabled": true }
  ]
}
```

### `PATCH /api/v1/notifications/preferences/`

Update one or more category preferences.

**Body:**
```json
{
  "assessments":    { "email_enabled": false },
  "collaboration":  { "email_enabled": true, "push_enabled": false }
}
```

**Response:**
```json
{ "success": true, "message": "Preferences updated.", "data": [ ... ] }
```
