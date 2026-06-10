# 13) Multi-Instructor Collaboration & Owner Protection

## Overview

A single `NidusCourse` can be co-authored by multiple verified instructors. One instructor is the **owner**; the rest are **co-instructors**. Ownership is permanent for the course's lifetime (unless explicitly transferred — see Future Extensions).

---

## Data Model

```
NidusCourse
  ├── created_by          (FK → User)                    ← owner; set once at creation, immutable via API
  ├── instructors         (M2M → User)                   ← all who can read/edit content; owner is always a member (instructor courses only)
  └── partner_institution (FK → PartnerInstitutionProfile, nullable)  ← set automatically when a partner institution creates the course
```

`created_by` is set to `request.user` inside `NidusCourseCreateUpdateSerializer.create()` and is not exposed in the writable serializer fields (`read_only_fields = ['created_by']`), so it cannot be changed via the API.

`partner_institution` is set automatically at course creation when the creator is a `partner_institution` user. It is never writable via the API — not even by the owner.

---

## What Each Role Can Do

| Action | Owner (`created_by`) | Co-instructor | Admin |
|--------|---------------------|---------------|-------|
| Edit title, description, price, etc. | Yes | Yes | Via admin panel |
| Add / remove sections | Yes | Yes | Via admin panel |
| Add / remove lectures, quizzes, assignments, coding exercises | Yes | Yes | Via admin panel |
| Upload videos | Yes | Yes | Via admin panel |
| **Add / remove instructors** | **Yes** | **No** | Via admin panel |
| Submit for review | Yes | Yes | N/A |
| Rework after rejection | Yes | Yes | N/A |
| Archive course | Yes | Yes | Yes |
| Restore from archive | Yes | Yes | Yes |
| Delete course | N/A (no delete endpoint) | N/A | Via admin panel |

`partner_institution` is set at course creation by the system — it is never writable via the API. Only an admin can change it via the Django admin panel.

---

## Enforcement Points

### 1. Serializer — `NidusCourseCreateUpdateSerializer`

**File:** `courses/all_serializers/course_serializers.py`

`instructors` is not a writable field on this serializer. Any `instructors` key in a POST or PATCH body is silently ignored by DRF — the field does not exist in `Meta.fields`. The roster can only change via the invitation flow.

On `create()`, the creator is automatically added:
- **Instructor creator**: `course.instructors.set([request_user])` — owner seeded into M2M.
- **Partner institution creator**: `partner_institution` FK set; M2M left empty (partner institution users are never in `instructors`).

### 2. Utility guard — `guard_owner()` in `courses/utils.py`

A reusable guard for any future endpoint that must be owner-only (e.g., a course delete endpoint).

```python
def guard_owner(course, user):
    """Return a 403 Response if user is not the course owner, else None."""
    if course.created_by != user:
        return Response(
            {'success': False, 'message': 'Only the course owner can perform this action.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None
```

Usage pattern (follows the same convention as `guard_editable`):

```python
def delete(self, request, pk):
    course = self._get_course(request, pk)
    if err := guard_owner(course, request.user):
        return err
    course.delete()
    return Response({'success': True, 'message': 'Course deleted.'})
```

### 3. Owner is always in the instructor list

On `create()`, the owner (instructor type) is seeded via `course.instructors.set([request_user])`. On `accept_instructor_invite()` in `invite_service.py`, co-instructors are added atomically. No code path calls `instructors.set(...)` without the owner being present (because only `create()` seeds the M2M, and the owner is always the argument).

### 4. `created_by` is immutable

`created_by` is in `read_only_fields` on `NidusCourseCreateUpdateSerializer.Meta` — the field is present on the model but not writable through the API. Any attempt to pass it in a PATCH body is silently ignored by DRF.

---

## How Instructors Are Added

Co-instructors can **only** be added via the invitation flow. Passing `instructors` in a POST or PATCH body has no effect — the field is not declared on `NidusCourseCreateUpdateSerializer`.

The owner sends an invite by email. The invitee receives an email with a **View Invitation** link and accepts or declines on the platform. Only on accept is the invitee added to `course.instructors`.

See the **Invitation Flow** section below for full details.

---

## Request Flow: Co-instructor PATCH

```
PATCH /api/v1/courses/{pk}/
  instructor A (co-instructor) sends:
    { "title": "New Title" }

CourseDetailView._get_course(request, pk)
  → filters NidusCourse where pk=pk AND instructors=A  ✓ (A is in M2M)

guard_editable(course)  → None (course is draft)

NidusCourseCreateUpdateSerializer.update()
  → title = "New Title"  ← applied
  → roster unchanged (instructors field not accepted by serializer)

Response: 200 OK, title updated, roster unchanged
```

Passing `instructors: [...]` in the body is silently ignored — DRF strips unknown fields before `update()` is called.

---

## Invitation Flow

### Model — `CourseInstructorInvite`

**File:** `courses/all_models/course_models.py`

```
CourseInstructorInvite
  ├── course        (FK → NidusCourse)
  ├── invited_by    (FK → User)           ← must be course.created_by
  ├── invited_user  (FK → User)           ← verified instructor resolved from email
  ├── token         (UUID, unique)        ← included in the email link
  ├── status        pending | accepted | declined | expired | revoked
  ├── expires_at    (DateTimeField)       ← now + INSTRUCTOR_INVITE_EXPIRY_DAYS days
  └── responded_at  (DateTimeField, nullable)
```

Partial unique index (`unique_pending_invite_per_course`) prevents a second pending invite to the same user on the same course while one is already open. Declined/expired/revoked invites do not block a fresh invite.

### State Machine

```
                    ┌─────────────────────────────────────────────────────┐
                    │  owner calls DELETE .../invites/<id>/               │
                    ▼                                                     │
               ┌─────────┐                                               │
               │ revoked │                                               │
               └─────────┘                                               │
                                                                         │
 ┌─────────┐ ──────────────────────────────────────────────────────────► │
 │ pending │                                                             │
 └─────────┘ ──► ┌──────────┐  invitee POSTs accept + course editable   │
      │          │ accepted │  (locked row — select_for_update)          │
      │          └──────────┘                                            │
      │                                                                  │
      ├───────► ┌──────────┐  invitee POSTs decline                     │
      │         │ declined │  (locked row — select_for_update)           │
      │         └──────────┘                                             │
      │                                                                  │
      └───────► ┌─────────┐  Celery Beat: expire_instructor_invites_task │
                │ expired │  bulk UPDATE WHERE expires_at < now, hourly  │
                └─────────┘                                              │
```

### End-to-End Flow

```
OWNER (verified instructor / partner institution)
───────────────────────────────────────────────────────────────────────────

  POST /api/v1/courses/<pk>/instructors/invite/
  body: { "email": "co.instructor@example.com" }

  Guards (fail fast, in order):
    ├─ course not found or caller != created_by         → 404
    ├─ course.status not in (draft, rejected)           → 422
    ├─ email not a verified instructor on platform      → 400
    ├─ email == owner's own email                       → 400
    ├─ invitee already in course.instructors            → 400
    └─ pending invite already exists for this invitee  → 400 (or 400 if DB
                                                             constraint race)

  201 Created ──────────────────────────────────────────────────────────►
  CourseInstructorInvite row created:
    status    = pending
    expires_at = now + INSTRUCTOR_INVITE_EXPIRY_DAYS (default 7)
    token     = UUID (not exposed in owner response)

  transaction.on_commit ──► send_instructor_invite_email_task.delay(invite.pk)
    Celery: fetch invite → render HTML email → send_mail()
    acks_late=True, up to 3 retries with backoff

                                   Email arrives in invitee's inbox:
                                   "View Invitation" → {FRONTEND_URL}/invites/{token}

 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  While PENDING, owner can list or revoke:

  GET  /api/v1/courses/<pk>/instructors/invites/?status=pending
    ├─ caller != created_by  → 404 (co-instructors see 404, not 403)
    └─ 200 { results: [...] }  (paginated, token field excluded)

  DELETE /api/v1/courses/<pk>/instructors/invites/<invite_id>/
    ├─ caller != created_by        → 404
    ├─ invite.status != pending    → 422
    └─ 200  invite.status → revoked
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  Celery Beat (hourly):
  expire_instructor_invites_task()
    UPDATE course_instructor_invites
       SET status = 'expired'
     WHERE status = 'pending' AND expires_at < now()
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─


INVITEE (verified instructor)
───────────────────────────────────────────────────────────────────────────

  GET  /api/v1/courses/invites/my/?status=pending
    └─ 200 { results: [...] }  (paginated, token included)

  POST /api/v1/courses/invites/<token>/accept/

    Inside transaction.atomic() + SELECT ... FOR UPDATE:
      ├─ token not found or belongs to another user  → 404
      ├─ invite.expires_at < now                     → 410  (invite has expired)
      ├─ invite.status != pending                    → 410  (no longer valid)
      ├─ course.status not in (draft, rejected)      → 422  (course no longer editable)
      └─ all pass:
           invite.status        → accepted
           invite.responded_at  → now
           course.instructors.add(invitee)           ← atomic, under row lock

    200 OK  — invitee is now a co-instructor

  ── OR ──

  POST /api/v1/courses/invites/<token>/decline/

    Inside transaction.atomic() + SELECT ... FOR UPDATE:
      ├─ token not found or belongs to another user  → 404
      ├─ invite.expires_at < now                     → 410
      ├─ invite.status != pending                    → 410
      └─ all pass:
           invite.status        → declined
           invite.responded_at  → now
           (invitee NOT added to instructors)

    200 OK  — record kept; owner sees it in GET ?status=declined


CO-INSTRUCTOR (after accepting)
───────────────────────────────────────────────────────────────────────────

  GET  /api/v1/courses/<pk>/            → full course detail (authoring surface)
  PATCH /api/v1/courses/<pk>/          → edit title, description, price, etc.
  POST  /api/v1/courses/<pk>/sections/ → add/reorder sections
  ...all content endpoints...

  Cannot:
    - Send, revoke, or list invites   (owner-only; co-instructor gets 404)
    - Change course.created_by        (read-only field)
```

### Endpoints

| Method | URL | Permission | Action |
|--------|-----|------------|--------|
| `POST` | `/courses/<pk>/instructors/invite/` | Owner only | Send invite by email |
| `GET` | `/courses/<pk>/instructors/invites/` | Owner only | List all invites (`?status=` filter) |
| `DELETE` | `/courses/<pk>/instructors/invites/<invite_id>/` | Owner only | Revoke pending invite |
| `GET` | `/courses/invites/my/` | Instructor | Received invites (default `?status=pending`) |
| `POST` | `/courses/invites/<token>/accept/` | Invitee | Accept — atomically added to `instructors` M2M |
| `POST` | `/courses/invites/<token>/decline/` | Invitee | Decline — record kept for owner audit |

### Service layer

**File:** `courses/services/invite_service.py`

- `create_instructor_invite(course, owner, email)` — validates, creates, dispatches `send_instructor_invite_email_task` via `transaction.on_commit`
- `revoke_instructor_invite(invite, owner)` — owner-only, pending-only
- `accept_instructor_invite(token, user)` — atomic: sets `status=accepted`, adds user to `course.instructors`
- `decline_instructor_invite(token, user)` — sets `status=declined`, record preserved

### Email & Celery

**Email:** `courses/email_utils.py` → `send_instructor_invite_email()` renders `templates/emails/instructor_invite.html`. The email contains a single **View Invitation** link (`{FRONTEND_URL}/invites/{token}`); accept/decline happen on the platform.

**Tasks** (`courses/tasks.py`):
- `send_instructor_invite_email_task(invite_id)` — `acks_late=True`, 3 retries with backoff; dispatched on commit.
- `expire_instructor_invites_task()` — bulk-updates `pending → expired` for rows past `expires_at`; runs hourly via `CELERY_BEAT_SCHEDULE`.

### Configuration

```env
INSTRUCTOR_INVITE_EXPIRY_DAYS=7   # days before a pending invite expires
```

Read in `settings.py` as `INSTRUCTOR_INVITE_EXPIRY_DAYS = env.int('INSTRUCTOR_INVITE_EXPIRY_DAYS', default=7)`.

---

## Future Extensions

| Feature | Description |
|---------|-------------|
| **Granular roles** | Per-instructor roles (`owner`, `editor`, `viewer`) via a through-model on the M2M. Different roles gate different actions. |
| **Transfer ownership** | Allow owner to transfer `created_by` to another instructor. Requires both parties to confirm. |
| **Activity log** | Track who changed what: "Instructor B edited Section 3". Useful for accountability. |
