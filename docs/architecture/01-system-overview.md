# 01) System Overview

## Architecture diagram

```
                        HTTP Request
                             │
                    ┌────────▼────────┐
                    │  Django Router  │
                    │ (urls.py root)  │
                    └────────┬────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
   /api/v1/auth/    /api/v1/courses/   /api/v1/verification/
          │                  │                  │
   ┌──────▼──────┐   ┌───────▼──────┐  ┌───────▼──────┐
   │authentication│  │   courses    │  │id_verification│
   │   app urls   │  │   app urls   │  │   app urls   │
   └──────┬──────┘   └───────┬──────┘  └───────┬──────┘
          │                  │                  │
   ┌──────▼──────────────────▼──────────────────▼──────┐
   │               View Layer (APIView)                 │
   │  all_views/ subdirectory per app                   │
   │  • auth, permissions, request parsing              │
   └──────────────────────┬─────────────────────────────┘
                          │
   ┌──────────────────────▼─────────────────────────────┐
   │              Serializer Layer                       │
   │  • field-level validation, shape, response format  │
   │  • NO business logic, NO DB writes                 │
   └──────────────────────┬─────────────────────────────┘
                          │
   ┌──────────────────────▼─────────────────────────────┐
   │            Service / Selector Layer                 │
   │  services/: domain operations (ordering, pipeline) │
   │  selectors/: reusable query helpers                 │
   └──────────────────────┬─────────────────────────────┘
                          │
   ┌──────────────────────▼─────────────────────────────┐
   │                  Model / DB Layer                   │
   │  Django ORM → PostgreSQL                            │
   │  • constraints, signals, state machines             │
   └─────────────────────────────────────────────────────┘
                          │
   ┌──────────────────────▼─────────────────────────────┐
   │            Async / Background Layer                 │
   │  Celery + Redis broker                              │
   │  • video transcoding (FFmpeg)                       │
   │  • assignment auto-grading (RubricGrader)           │
   │  • coding exercise evaluation (Docker sandbox)      │
   └─────────────────────────────────────────────────────┘
```

## Project layout

```
career_college_backend/
├── settings.py          # project config: DB, JWT, Celery, logging, DRF
├── urls.py              # root URL router

authentication/          # /api/v1/auth/
├── models.py            # User, LearnerProfile, InstructorProfile, PartnerInstitutionProfile
├── signals.py           # auto-create profile on user post_save
├── all_views/
│   ├── auth_views.py       # register, login, logout, token refresh
│   ├── otp_views.py        # OTP verify/resend
│   ├── password_views.py   # forgot/reset/change password
│   ├── google_views.py     # Google OAuth redirect/callback/exchange
│   ├── linkedin_views.py   # LinkedIn OAuth redirect/callback/exchange
│   └── profile_views.py    # private profile management + public browse
└── services/
    ├── google_oauth.py     # Google token exchange, user provisioning
    └── linkedin_oauth.py   # LinkedIn token exchange, user provisioning

courses/                 # /api/v1/courses/
├── all_models/
│   ├── course_models.py    # NidusCourse, CourseSection, SectionContent, CourseCategory
│   ├── content_models.py   # Lecture, VideoAsset, VideoProcessingJob, WatchProgress
│   ├── assessment_models.py # Quiz, QuizQuestion, QuizAnswer, QuizAttempt,
│   │                        # Assignment, AssignmentQuestion, AssignmentSubmission,
│   │                        # CodingExercise, CodingSubmission
│   └── enrollment_models.py # Enrollment
├── all_views/
│   ├── course_views.py     # course list/create/detail
│   ├── content_views.py    # sections, SectionContent, lectures, quizzes
│   ├── coding_views.py     # coding exercises, language configs, test cases
│   ├── assignment_views.py # assignment CRUD
│   ├── status_views.py     # submit/review/rework/archive state transitions
│   ├── enrollment_views.py # catalog, enroll, my-courses
│   └── learner_views.py    # /learn/ consumption surface
├── all_serializers/
│   ├── learner_serializers.py   # learner-safe (no solution_code, no is_correct)
│   └── enrollment_serializers.py
├── services/
│   ├── section_service.py       # reorder, video pipeline entry point
│   ├── assignment_service.py    # assignment CRUD, question reorder
│   ├── enrollment_service.py    # enroll/unenroll, progress recalculation, catalog
│   ├── learner_service.py       # access resolution, content loaders, submission helpers
│   ├── assignment_grading.py    # RubricGrader — deterministic criterion matchers
│   └── code_runner.py           # Docker sandbox, per-language harnesses
├── tasks.py             # Celery tasks: transcoding, grading, coding evaluation
└── transcoding.py       # FFmpeg routines (5 HLS renditions)

id_verification/         # /api/v1/verification/
├── models.py            # IdentityVerification state machine
└── all_views/
    ├── instructor_views.py  # draft/update/submit/my-list
    └── admin_views.py       # admin list/detail/review

core/
├── permissions.py       # all shared DRF permission classes
├── pagination.py        # StandardResultsSetPagination (page_size=10, max=100)
└── middleware.py        # custom middleware
```

## API route map

Defined in `career_college_backend/urls.py`:

| Prefix | App | Example paths |
|--------|-----|---------------|
| `/api/v1/auth/` | `authentication` | `/auth/register/`, `/auth/login/`, `/auth/otp/verify/` |
| `/api/v1/verification/` | `id_verification` | `/verification/create/`, `/verification/admin/list/` |
| `/api/v1/courses/` | `courses` | `/courses/catalog/`, `/courses/sections/{id}/contents/`, `/courses/learn/lectures/{id}/` |

## Design patterns

**APIView everywhere** — every view is an explicit `APIView` subclass with manual `get`, `post`,
`patch`, `delete` method definitions. No `ListAPIView`, no `RetrieveUpdateAPIView`, no `ViewSet`
or `ModelViewSet`. This makes HTTP method intent obvious and avoids the hidden magic of generic
views.

**`all_views/` + thin `views.py`** — real implementations live in `all_views/` subdirectories.
`views.py` is a thin re-export surface. New views always go in `all_views/` first, then get
imported into `views.py`.

**Serializers: shape and validation only** — serializers handle field-level validation and
response shaping. They never write to the database, call services, or trigger side effects. Business
logic lives exclusively in `services/`.

**Service/selector separation** — services own domain operations (create, mutate, orchestrate).
Selectors own reusable query logic (base querysets, scoped fetches). Views call services;
services may call selectors.

**`SectionContent` GenericForeignKey** — all curriculum item ordering (lectures, quizzes,
assignments, coding exercises) flows through `SectionContent`. Content objects have no `position`
field of their own. Adding a new content type requires no change to the ordering system.

**`core/` for shared code** — all permission classes, pagination, and middleware live in `core/`.
No per-app duplicates.

## Request lifecycle (step by step)

```
1. HTTP request arrives
   └── career_college_backend/urls.py routes to app urls.py

2. App urls.py matches pattern
   └── routes to view class in all_views/

3. View: authentication & permissions
   ├── DRF authenticates via JWT Bearer header or cookie
   ├── Permission classes run in order (IsAuthenticated, IsEmailVerified, etc.)
   └── 401 / 403 returned immediately if any check fails

4. View: request parsing
   ├── For writes: passes request.data to serializer
   └── For reads: builds queryset with ownership filters

5. Serializer: validation
   ├── Field types, required checks, custom validators
   ├── Returns serializer.errors → view returns 400 if invalid
   └── serializer.validated_data passed to service

6. Service: domain logic
   ├── Atomic DB operations, state machine transitions
   ├── Enqueues Celery tasks when async work is needed
   └── Raises ValidationError for business rule violations

7. Model / DB
   ├── ORM queries / saves
   ├── DB constraints (UniqueConstraint, CheckConstraint) enforce integrity
   └── Signals fire post-save (e.g., profile creation, progress recalculation)

8. View: response
   └── Always wraps in { "success": true/false, "message": "...", "data": {...} }
       (error shape follows FRONTEND_ERROR_RESPONSE_FORMAT.md)
```

## Important conventions

- Instructor ownership checks use queryset filters (`section__course__instructors=request.user`),
  not separate permission classes.
- `SectionContent.position` is the single source of truth for item order within a section.
- All permission classes live in `core/permissions.py`. None defined inside app directories.
- Slug-based URLs return **403** on access denial; numeric-ID URLs return **404**
  (to avoid confirming existence of non-public resources).
- All responses follow the `{ "success": true/false, ... }` envelope.
  See `FRONTEND_ERROR_RESPONSE_FORMAT.md` for the full shape.

## Why this design

- **Clear URL layering** makes large API surfaces navigable.
- **View/serializer/service separation** keeps each layer testable in isolation — services run
  without HTTP context, serializers run without side effects.
- **Shared utilities in `core/`** eliminate per-app drift in permission behavior.
- **Explicit `APIView` subclasses** make every endpoint's supported HTTP methods obvious — no
  implicit routing magic from `ViewSet` actions.
- **Async background tasks** (Celery + Redis) keep upload and submission endpoints fast by
  offloading heavy work (FFmpeg transcoding, Docker code evaluation, AI grading) to workers.
