# 01) System Overview

## Platform at a glance

A Django REST Framework backend for a course marketplace. Four user roles, seven apps, a layered
request path, and a Celery/Redis async tier behind a real-time WebSocket layer.

**Roles:** `learner` · `instructor` · `partner_institution` · `admin` (single custom `User` model,
`user_type` field; see [02-auth-and-accounts.md](02-auth-and-accounts.md)).

| App | Prefix | Responsibility | Deep dive |
|-----|--------|----------------|-----------|
| `authentication` | `/api/v1/auth/` | Registration, OTP, JWT, OAuth, profiles, partner-institution experts & departments | [02](02-auth-and-accounts.md), [03](03-profiles.md), [18](18-partner-institutions.md) |
| `courses` | `/api/v1/courses/` | Catalog, authoring (instructor + institution), curriculum, video, quizzes, assignments, coding, enrollment, certificates, reviews | [04](04-courses-and-curriculum.md)–[06](06-quizzes.md), [09](09-coding-exercises.md)–[12](12-enrollment.md), [14](14-certificate-system.md), [15](15-review-rating-system.md) |
| `id_verification` | `/api/v1/verification/` | Instructor identity **and** partner-institution credential verification state machines | [07](07-id-verification.md), [18](18-partner-institutions.md) |
| `messaging` | `/api/v1/messaging/` | Learner ↔ instructor direct messaging (REST + WebSocket) | [17](17-messaging-system.md) |
| `notifications` | `/api/v1/notifications/` | In-app notification feed, email preferences, `dispatch()` API | [16](16-notification-system.md) |
| `realtime` | `/ws/` | ASGI `PlatformConsumer` multiplexing the notifications + messaging WS streams | [16](16-notification-system.md), [17](17-messaging-system.md) |
| `core` | — | Shared permissions, pagination, middleware | [08](08-core-infrastructure.md) |

**Stack:** Django 5 + DRF · PostgreSQL · Simple JWT + django-allauth (OAuth) · Celery + Redis ·
Django Channels + Redis channel layer (ASGI) · FFmpeg (HLS transcoding) · Docker + gVisor (code sandbox).

---

## Architecture diagram

```
        HTTP request (Bearer JWT / cookie)                WebSocket  ws://…/ws/?token=<JWT>
                     │                                              │
            ┌────────▼────────┐                          ┌──────────▼──────────┐
            │  WSGI (gunicorn)│                          │  ASGI (daphne)      │
            │  Django router  │                          │  ProtocolTypeRouter │
            └────────┬────────┘                          │  → PlatformConsumer │
                     │                                    └──────────┬──────────┘
   ┌─────────┬───────┼────────┬──────────┬──────────┐               │ streams: notifications, messaging
   │         │       │        │          │          │               │ (JWT validated on connect → 4001)
 /auth/  /courses/ /verif. /messaging/ /notif.    (admin)           │
   │         │       │        │          │                          │
   └─────────┴───────┴────────┴──────────┴───────► View Layer ◄──────┘
                                          (APIView, all_views/ per app)
                                          • authentication & permission classes
                                          • request parsing
                                                     │
                                          ┌──────────▼───────────┐
                                          │   Serializer Layer    │  shape + field validation only
                                          │   (NO business logic) │
                                          └──────────┬───────────┘
                                                     │
                                          ┌──────────▼───────────┐
                                          │ Service / Selector    │  domain ops, state machines,
                                          │ Layer (services/)     │  enqueue async work, *Error classes
                                          └──────────┬───────────┘
                                                     │
                                          ┌──────────▼───────────┐
                                          │   Model / DB Layer    │  Django ORM → PostgreSQL
                                          │   constraints·signals·│  transition_to() state machines
                                          │   GenericFK ordering  │
                                          └──────────┬───────────┘
                                                     │ transaction.on_commit
                                          ┌──────────▼───────────┐
                                          │ Async / Realtime tier │
                                          │ Celery + Redis broker │  • video transcoding (FFmpeg)
                                          │ Celery beat (reaper)  │  • assignment auto-grading
                                          │ Channels channel layer│  • coding eval (Docker/gVisor)
                                          │   (group_send → WS)   │  • email send · WS fan-out
                                          └───────────────────────┘
```

Two entry points share the same view/service/model stack: **WSGI** (gunicorn) serves all REST traffic;
**ASGI** (daphne) serves the WebSocket. Cross-process realtime delivery (e.g. a Celery worker pushing a
`new_message` to a connected client) goes through the Redis **channel layer**, not an in-process call.

---

## Project layout

```
career_college_backend/
├── settings.py          # DB, JWT, Celery, Channels (CHANNEL_LAYERS), logging, DRF, task routes
├── urls.py              # root URL router (REST)
├── asgi.py              # ProtocolTypeRouter: http → Django, websocket → realtime
├── wsgi.py              # gunicorn entry point
└── celery.py            # Celery app + beat schedule

authentication/          # /api/v1/auth/
├── models.py            # User, LearnerProfile, InstructorProfile, PartnerInstitutionProfile, Department
├── signals.py           # auto-create profile on user post_save
├── tasks.py             # async OTP + expert-credentials email
├── all_views/
│   ├── auth_views.py        # register, login, logout, token refresh
│   ├── otp_views.py         # OTP verify/resend
│   ├── password_views.py    # forgot/reset/change password
│   ├── google_views.py / linkedin_views.py  # OAuth redirect/callback/exchange
│   ├── profile_views.py     # private profile mgmt + public browse
│   └── partner_views.py     # institution experts + departments (IsVerifiedPartnerInstitution)
└── services/
    ├── google_oauth.py / linkedin_oauth.py / user_provisioning.py
    ├── expert_service.py    # provision_expert, update_expert, set_expert_active
    └── department_service.py# department CRUD, resolve_expert_department

courses/                 # /api/v1/courses/
├── all_models/
│   ├── course_models.py      # NidusCourse, CourseSection, SectionContent, CourseCategory
│   ├── content_models.py     # Lecture, VideoAsset, VideoProcessingJob, WatchProgress
│   ├── assessment_models.py  # Quiz/Question/Answer/Attempt, Assignment(+Submission), CodingExercise(+Submission)
│   ├── enrollment_models.py  # Enrollment
│   ├── certificate_models.py # Certificate (UUID, on-the-fly PDF)
│   └── review_models.py      # CourseReview, ReviewVote
├── all_views/
│   ├── course_views.py / status_views.py / content_views.py
│   ├── coding_views.py / assignment_views.py
│   ├── enrollment_views.py   # catalog, enroll, my-courses
│   ├── learner_views.py      # /learn/ consumption surface
│   ├── certificate_views.py / review_views.py
│   ├── invite_views.py       # co-instructor invite/accept
│   └── institution_course_views.py  # partner-institution roster add/remove
├── all_serializers/          # modular, incl. learner_serializers (no solution_code / is_correct)
├── services/
│   ├── section_service.py / curriculum_service.py / assignment_service.py
│   ├── enrollment_service.py # enroll/unenroll, recalculate_progress, catalog filters
│   ├── learner_service.py    # access resolution, content loaders, submission helpers
│   ├── assignment_grading.py # RubricGrader (deterministic criterion matchers)
│   ├── code_runner.py        # Docker/gVisor sandbox, per-language batched harness
│   ├── certificate_service.py / review_service.py
│   └── invite_service.py / institution_course_service.py
├── tasks.py             # transcoding, grading, coding eval (run/submit), zombie reaper
└── transcoding.py       # FFmpeg routines (5 HLS renditions)

id_verification/         # /api/v1/verification/
├── models.py            # IdentityVerification + InstitutionVerification state machines
└── all_views/
    ├── instructor_views.py / admin_views.py     # identity (instructor) flow
    └── institution_views.py                     # partner-institution credential flow

messaging/               # /api/v1/messaging/
├── models.py            # Conversation, Message (cursor-based unread tracking)
├── services/messaging_service.py  # send-gate, send/read, unread counts, WS push + notify
└── all_views/conversation_views.py

notifications/           # /api/v1/notifications/
├── models.py            # Notification, NotificationEventType, preferences
├── services/
│   ├── dispatcher.py    # dispatch() — the single entry point for emitting notifications
│   ├── builders.py      # per-event payload builders
│   └── preference_service.py
├── tasks.py             # async notification email
└── views.py             # feed, unread-count, mark-read, preferences

realtime/                # /ws/  (ASGI only)
├── consumers.py         # PlatformConsumer — multiplexes streams, routes channel events
├── routing.py           # websocket_urlpatterns (^ws/$)
├── middleware.py        # JWTAuthMiddlewareStack (?token= validation)
└── streams/             # base + notifications_stream + messaging_stream handlers

core/
├── permissions.py       # ALL shared DRF permission classes
├── pagination.py        # StandardResultsSetPagination (page_size=10, max=100)
└── middleware.py
```

---

## API route map

REST prefixes defined in `career_college_backend/urls.py`; WebSocket in `realtime/routing.py`:

| Prefix | App | Example paths |
|--------|-----|---------------|
| `/api/v1/auth/` | `authentication` | `/auth/register/`, `/auth/login/`, `/auth/partner/experts/`, `/auth/partner/departments/` |
| `/api/v1/verification/` | `id_verification` | `/verification/create/`, `/verification/institution/create/`, `/verification/admin/institution/{id}/review/` |
| `/api/v1/courses/` | `courses` | `/courses/catalog/`, `/courses/learn/lectures/{id}/`, `/courses/{slug}/reviews/`, `/courses/{pk}/institution-instructors/` |
| `/api/v1/messaging/` | `messaging` | `/messaging/conversations/`, `/messaging/conversations/create/`, `/messaging/conversations/{id}/read/` (follow-up sends are WebSocket-only) |
| `/api/v1/notifications/` | `notifications` | `/notifications/`, `/notifications/unread-count/`, `/notifications/preferences/` |
| `/api/v1/webinars/` | `webinars` | `/webinars/catalog/`, `/webinars/create/`, `/webinars/{pk}/publish/`, `/webinars/{slug}/register/` |
| `/api/v1/analytics/` | `analytics` | `/analytics/partner/summary/`, `/analytics/partner/enrollments/trend/`, `/analytics/partner/top-courses/` |
| `/api/v1/payments/` | `payments` | `/payments/checkout/`, `/payments/ipn/`, `/payments/success/`, `/payments/orders/` |
| `/api/v1/admin-console/` | `admin_console` | `/admin-console/auth/session/`, `/admin-console/sessions/`, `/admin-console/users/`, `/admin-console/audit/` (login is the shared `/auth/login/`) |
| `/ws/` | `realtime` | `ws://host/ws/?token=<JWT>` — streams `notifications`, `messaging` |
| `/admin/` | Django admin | staff console |

---

## Design patterns

**APIView everywhere** — every view is an explicit `APIView` subclass with manual `get`/`post`/
`patch`/`delete`. No `ListAPIView`, `RetrieveUpdateAPIView`, `ViewSet`, or `ModelViewSet`. HTTP method
intent stays obvious; no hidden generic-view routing.

**`all_views/` + thin `views.py`** — real implementations live in `all_views/`; `views.py` re-exports.

**Serializers: shape & validation only** — no DB writes, no service calls, no side effects. Business
logic lives exclusively in `services/`.

**Service/selector separation + typed errors** — services own domain operations and raise dedicated
exceptions carrying an HTTP status (`AssignmentSubmissionError`, `ReviewError`, `InviteError`,
`ExpertError`, `DepartmentError`, `InstitutionCourseError`, `CodingSubmissionError`). Views translate
`exc.http_status` directly into the response.

**State machines in the model** — `NidusCourse.transition_to()`, `IdentityVerification.transition_to()`,
and `InstitutionVerification.transition_to()` are the *only* entry points for status changes; side
effects (e.g. flipping `is_verified`) live inside the transition so they fire regardless of caller.

**`SectionContent` GenericForeignKey** — all curriculum ordering (lectures, quizzes, assignments,
coding exercises) flows through `SectionContent`; content models carry no `position` of their own.
Adding a content type needs no change to the ordering system.

**Async via `transaction.on_commit`** — Celery tasks, notification dispatch, and WS fan-out are
scheduled on commit so a rolled-back transaction never leaks a phantom task / notification / push.

**One multiplexed WebSocket** — a single `PlatformConsumer` at `/ws/` carries every feature stream
(`{"stream": "...", "payload": {...}}`). Cross-process delivery uses the Redis channel layer
(`group_send`), so a Celery worker can push to a connected client. Add a stream by registering a
handler in `realtime/streams/`.

**`core/` for shared code** — all permission classes, pagination, and middleware live in `core/`. No
per-app duplicates.

---

## Request lifecycle (REST, step by step)

```
1. HTTP request arrives
   └── career_college_backend/urls.py routes to app urls.py

2. App urls.py matches pattern → view class in all_views/

3. View: authentication & permissions
   ├── DRF authenticates via JWT Bearer header or cookie
   ├── Permission classes run in order (IsAuthenticated, IsEmailVerified, IsVerifiedCourseCreator, …)
   └── 401 / 403 / 404 returned immediately if a check fails (slug → 403, numeric id → 404)

4. View: request parsing
   ├── writes  → request.data to serializer
   └── reads   → queryset with ownership filters

5. Serializer: validation
   ├── field types, required checks, custom validators
   ├── invalid → view returns 400 with errors
   └── validated_data passed to a service

6. Service: domain logic
   ├── atomic DB ops, state-machine transitions
   ├── enqueues Celery tasks / schedules on_commit side effects
   └── raises a typed *Error (→ 4xx) or ValidationError (business rule → 422)

7. Model / DB
   ├── ORM queries / saves; DB constraints enforce integrity
   └── signals fire post-save (profile creation, progress recalculation, avg-rating recalc, …)

8. View: response
   └── { "success": true/false, "message": "...", "data": {...} }   (errors per FRONTEND_ERROR_RESPONSE_FORMAT.md)
```

**WebSocket lifecycle:** connect → `JWTAuthMiddlewareStack` validates `?token=` (close `4001` on
failure) → `PlatformConsumer` joins per-user groups → inbound frames dispatched by `stream` to a
handler (ORM wrapped in `database_sync_to_async`) → outbound pushes arrive via the channel layer.

---

## Important conventions

- Course-creator ownership uses queryset filters (`Q(instructors=user) | Q(created_by=user)`), not
  bespoke per-object permission classes.
- `SectionContent.position` is the single source of truth for item order within a section.
- All permission classes live in `core/permissions.py`. None inside app directories.
- **Slug-based URLs → 403 on access denial; numeric-ID URLs → 404** (don't confirm existence of
  non-public resources). Applies to both learner and authoring endpoints.
- All responses follow the `{ "success": true/false, ... }` envelope (see `FRONTEND_ERROR_RESPONSE_FORMAT.md`).
- Side effects that must not fire on rollback are scheduled with `transaction.on_commit`.

---

## Why this design

- **Clear URL + layer separation** keeps a large API surface navigable and each layer testable in
  isolation — services run without HTTP context, serializers run without side effects.
- **Typed service errors** give views a uniform, leak-free way to map domain failures to status codes.
- **State-machine-in-model** guarantees the link between status and side effects (verification →
  `is_verified`, completion → certificate) regardless of which code path triggers the transition.
- **Shared `core/` utilities** eliminate per-app drift in permission and pagination behaviour.
- **Async + realtime tiers** keep request handlers fast: heavy work (FFmpeg, Docker eval, grading,
  email) runs on Celery workers, and live updates fan out over the Redis channel layer to one
  multiplexed WebSocket instead of N per-feature sockets.
```
