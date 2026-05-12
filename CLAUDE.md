# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Keep this file current.** Whenever you rename a symbol, add a new pattern, change a convention, or introduce an architectural rule, update the relevant section of this file in the same change. Stale guidance is worse than no guidance.

## Project Overview

A Django REST Framework backend for a course marketplace platform (Coursera-like). Users can be learners, instructors, partner institutions, or admins. Instructors create courses with mixed content (lectures, quizzes, coding exercises), upload videos that are async-transcoded to HLS, and must pass identity verification before publishing.

## Development Setup

**Database:** PostgreSQL (not SQLite). Set `DATABASE_URL` (or the individual `DB_*` vars) in `.env` before running migrations.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # fill in values — including Postgres credentials
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Celery worker (required for video transcoding):
```bash
celery -A career_college_backend worker -l info
```

## Key Commands

```bash
python manage.py migrate
python manage.py makemigrations
python manage.py test                    # all tests
python manage.py test courses            # single app
python manage.py test authentication.tests  # single test module
python manage.py check                   # Django system checks

# Data repair commands
python manage.py populate_section_content --dry-run
python manage.py reindex_section_content_positions --dry-run
```

## Architecture

### Apps

| App | Path prefix | Responsibility |
|-----|-------------|----------------|
| `authentication` | `/api/v1/auth/` | Registration, OTP, JWT, OAuth (Google/LinkedIn), profiles |
| `courses` | `/api/v1/courses/` | Course authoring, curriculum, lectures, video pipeline, quizzes, coding exercises |
| `id_verification` | `/api/v1/verification/` | Instructor identity verification state machine |
| `core` | — | Shared permissions, pagination, middleware |

### Custom User Model

`authentication/models.py` — email-based (no username field), with `user_type` field: `learner`, `instructor`, `partner_institution`, `admin`. On creation, a signal (`authentication/signals.py`) auto-creates the matching profile (`LearnerProfile`, `InstructorProfile`, or `PartnerInstitutionProfile`). `AUTH_USER_MODEL = 'authentication.User'`.

### Course Content Ordering: SectionContent

The `SectionContent` model (in `courses/`) is the **single source of truth for ordering** within a section. It holds a `GenericForeignKey` that points to a `Lecture`, `Quiz`, or `CodingExercise`. When adding new content types, create the model and then create a `SectionContent` row linking it into the section — do not add ordering directly to content models. Each content model must have a `GenericRelation` to `SectionContent` so that deleting the object cascades and removes its curriculum slot automatically. Reordering logic lives in `courses/services/section_service.py` → `reorder_section_content()`.

### Video Pipeline

1. Client uploads raw video → `VideoAsset` created with status `uploading`
2. Celery task `transcode_video_asset_task` (`courses/tasks.py`) picks it up
3. FFmpeg (`courses/transcoding.py`) produces 5 HLS renditions: 240p, 360p, 480p, 720p, 1080p
4. Output written to `media/courses/{course_slug}/lectures/{lecture_id}/hls/{video_asset_id}/`
5. `VideoAsset.status` transitions: `uploading → processing → ready | failed`
6. `VideoProcessingJob` tracks per-job metadata

`FFMPEG_BINARY_PATH` and `FFPROBE_BINARY_PATH` env vars must point to installed binaries.

### Identity Verification State Machine

`IdentityVerification` states: `draft → submitted → under_review → approved | rejected | action_required → (expired)`. Approval auto-sets `InstructorProfile.is_verified = True`. Admin transitions are in `id_verification/views.py`.

### Permissions (core/permissions.py)

Custom DRF permission classes used across views:

- `IsPlatformAdmin` — `is_staff` or `user_type == admin`; used by admin-only actions like course review
- `IsEmailVerified` — gates most authenticated endpoints
- `IsInstructorUser` — `user_type == instructor`
- `IsVerifiedInstructor` — instructor with approved `IdentityVerification`
- `IsCourseInstructor` — object-level: user is in `course.instructors.all()`

**All permission classes must live in `core/permissions.py`.** Do not define permissions inside individual app directories. If a permission is specific to one app today but could plausibly guard another resource tomorrow, it still belongs in `core/`.

### Course Status State Machine

`NidusCourse.transition_to(new_status, reviewer=None, rejection_reason='')` in `courses/models.py` is the single entry point for all status changes. Valid transitions:

| From | To | Who |
|------|----|-----|
| `draft` | `under_review` | Instructor (via `/submit/`) |
| `under_review` | `published` | Admin (via `/review/` with `action: approve`) |
| `under_review` | `rejected` | Admin (via `/review/` with `action: reject`) |
| `rejected` | `draft` | Instructor (via `/rework/`) |
| `published` | `archived` | Instructor or Admin (via `/archive/`) |
| `archived` | `draft` | Instructor or Admin (via `/archive/` → rework) |

`draft → under_review` runs `_validate_course_completeness()`: checks title/description, at least one section, each section has content, all videos `status=ready`, all quizzes have questions with correct answers.

**Never set `status` directly on `NidusCourse` outside of `transition_to()`.**

### Reusable Entities in core/

The `core` app is the home for anything shared across two or more apps:

- **Permissions** → `core/permissions.py`
- **Pagination** → `core/pagination.py`
- **Middleware** → `core/middleware.py`
- **Shared base classes, mixins, utilities** → `core/` (e.g. a shared `_PaginatedListMixin`)

Do not duplicate these in individual apps. If you find yourself writing a permission class, paginator, or utility inside `authentication/`, `courses/`, or `id_verification/`, move it to `core/` and import from there.

### JWT & Auth Flow

- Access token: 12 h lifetime, `Bearer` header
- Refresh token: 7-day lifetime, rotation + blacklist enabled
- Tokens returned as JSON body **and** optionally as HttpOnly cookies (see `authentication/utils/cookie_helpers.py`)
- Token refresh: `POST /api/v1/auth/token/refresh/`
- OAuth: authorization-code flow for Google and LinkedIn; callback URLs configured via env vars

### View-Layer Helpers (courses/utils.py)

Reusable view-layer helpers that are not business logic and not DRF permissions belong in `courses/utils.py`. Do **not** define the same helper function in multiple `all_views/` modules — if two views need the same guard, response builder, or inline utility, extract it once and import it everywhere.

Example: `guard_editable(course)` in `courses/utils.py` is imported by `course_views.py`, `content_views.py`, `coding_views.py`, and `assignment_views.py`. A single definition means a single place to change the message or status code.

Rule of thumb: if you copy-paste a function between view files, stop and move it to `courses/utils.py` instead.

### View File Convention

Each app uses an `all_views/` subdirectory for the actual view implementations. `views.py` is a thin re-export. New views go in `all_views/`, then get imported into `views.py`.

**Always use `APIView` directly.** Do not use generic views (`ListAPIView`, `RetrieveUpdateAPIView`, etc.) or `ViewSet`/`ModelViewSet`. Every view in the project is an explicit `APIView` subclass with manual method definitions (`get`, `post`, `patch`, `delete`).

### Serializers

Business logic (cross-model validation, service calls) lives in `courses/services/` (split into `section_service.py` and `assignment_service.py`, re-exported via `__init__.py`) and `authentication/services/`, not in serializers. Serializers handle shape and field-level validation only.

### Response Format

**All responses — success and error — must follow this envelope:**

```python
# Success (single object or action)
return Response(
    {'success': True, 'message': 'Course created.', 'data': serializer.data},
    status=status.HTTP_201_CREATED,
)

# Success (no body needed)
return Response({'success': True, 'message': 'Deleted.'}, status=status.HTTP_200_OK)
```

**Error responses** follow the RFC 7807 shape documented in `FRONTEND_ERROR_RESPONSE_FORMAT.md`:

```python
# Validation error (400)
return Response(
    {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
    status=status.HTTP_400_BAD_REQUEST,
)

# Not found (404)
return Response(
    {'success': False, 'message': 'Course not found.'},
    status=status.HTTP_404_NOT_FOUND,
)

# Business logic violation (422)
return Response(
    {'success': False, 'message': 'Course is already published.'},
    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
)

# Server error (500)
return Response(
    {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
)
```

Never leak exception details or stack traces into the `message` field.

### Paginated Response Format

Use `StandardResultsSetPagination` from `core/pagination.py` (page size 10, max 100, configurable via `?page_size=N`). Wrap the paginator output with the standard `success` envelope:

```python
from core.pagination import StandardResultsSetPagination

def get(self, request):
    queryset = SomeModel.objects.filter(...)
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = SomeSerializer(page, many=True)
    paginated_response = paginator.get_paginated_response(serializer.data)
    paginated_response.data = {'success': True, 'data': paginated_response.data}
    return paginated_response
```

The inner `data` object has the standard DRF shape:

```json
{
  "success": true,
  "data": {
    "count": 42,
    "next": "http://localhost:8000/api/v1/courses/?page=3",
    "previous": "http://localhost:8000/api/v1/courses/?page=1",
    "results": []
  }
}
```

### Try-Except Pattern

Validate with the serializer first (no try-except needed for that). Wrap only the operations that can genuinely fail at runtime (DB writes, external calls, token generation, file I/O) in a try-except. Always log before returning a 500.

```python
import logging
logger = logging.getLogger(__name__)

class MyCourseView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified, IsInstructorUser]

    def post(self, request):
        serializer = MyCourseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'success': False, 'message': 'Validation failed.', 'errors': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            course = create_course(request.user, serializer.validated_data)
        except Exception as e:
            logger.error(f"Course creation failed for user {request.user.id}: {e}")
            return Response(
                {'success': False, 'message': 'An unexpected error occurred. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {'success': True, 'message': 'Course created.', 'data': NidusCourseSerializer(course).data},
            status=status.HTTP_201_CREATED,
        )
```

Do not catch broad `Exception` outside of try blocks that wrap genuinely risky operations. Object-not-found cases use a guard before the try block, not an except clause:

```python
try:
    course = NidusCourse.objects.get(pk=pk, instructors=request.user)
except NidusCourse.DoesNotExist:
    return Response(
        {'success': False, 'message': 'Course not found.'},
        status=status.HTTP_404_NOT_FOUND,
    )
```

**Domain `ValidationError` from state machines** (e.g. `transition_to()`) raises two forms — handle them differently:

- `message_dict` present → field-level constraint violation → **400** with `errors`
- plain string → state-machine / business-rule violation → **422** (no `errors` key)

Use `e.messages[0]` (always a safe string) instead of `str(e.message)` (can render as a list repr).

```python
except ValidationError as e:
    if hasattr(e, 'message_dict'):
        return Response(
            {'success': False, 'message': 'Action failed.', 'errors': e.message_dict},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(
        {'success': False, 'message': e.messages[0]},
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )
```

## Environment Variables

Critical ones not obvious from the code:

| Variable | Notes |
|----------|-------|
| `FFMPEG_BINARY_PATH` | Absolute path to `ffmpeg` binary |
| `FFPROBE_BINARY_PATH` | Absolute path to `ffprobe` binary |
| `CELERY_BROKER_URL` | Redis URL, e.g. `redis://127.0.0.1:6379/0` |
| `JWT_COOKIE_SECURE` | `False` for local HTTP dev |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | From Google Cloud Console |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | From LinkedIn Developer portal |
| `FRONTEND_GOOGLE_CALLBACK` / `FRONTEND_LINKEDIN_CALLBACK` | Frontend redirect after OAuth |

For local dev, `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` prints OTP emails to the terminal instead of sending them.

## Docs

Detailed design rationale is in `docs/architecture/` (10 files). `09-workflows-and-architecture-why.md` explains the reasoning behind each major workflow and is worth reading before making structural changes. `10-coding-exercises.md` covers the coding exercise data model, authoring API, and design decisions. `FRONTEND_ERROR_RESPONSE_FORMAT.md` defines the error shape all views must follow.
