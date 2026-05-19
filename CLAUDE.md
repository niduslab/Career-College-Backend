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

### Course Detail Endpoints

Four distinct course detail surfaces, split by audience and concern. Do not collapse them into one conditional endpoint:

| Endpoint | Audience | Purpose |
|---|---|---|
| `GET /catalog/<slug>/` (`CatalogCourseDetailView`, `AllowAny`) | Guests / unenrolled | Marketing page — course metadata + full curriculum outline (titles, durations) + preview lecture HLS URLs only for `Lecture.is_preview=True`. Stays as a one-shot tree because catalog browsing is a guest workflow and SEO benefits from a single page render. |
| `GET /my-courses/<slug>/` (`MyCoursesDetailView`, `IsEmailVerified`) | Enrolled learner OR course's own instructor | **Slim metadata only**: title, description, instructors, learning objectives/prerequisites/audiences, totals, plus the caller's enrollment block (progress %, completed_at, last_accessed_at) and `is_instructor` flag. Curriculum tree and per-item content **do not** live here — fetch them from `/learn/<slug>/curriculum/` and `/learn/<thing>/<id>/`. |
| `GET /learn/<slug>/curriculum/` (`LearnerCurriculumView`) | Same as above | Sidebar curriculum outline (sections + items, lightweight). See *Learner Consumption Endpoints* below. |
| `GET /<int:pk>/` (`CourseDetailView`, `IsVerifiedInstructor`) | Course's own instructor | Authoring/edit surface (GET + PATCH metadata). Curriculum edits flow through `/sections/`, `/lectures/`, `/contents/`. |

The Udemy-style course-player page is composed on the frontend from `/my-courses/<slug>/` (header card) + `/learn/<slug>/curriculum/` (sidebar) + `/learn/<thing>/<id>/` (per-item content). Do not bring back the one-shot consumption tree on `/my-courses/<slug>/` — it scales poorly for large courses and duplicates the curriculum endpoint's job.

Catalog bulk-loading lives in `courses/services/curriculum_service.py` → `load_catalog_curriculum(course)`. Consumed by `CatalogCourseDetailSerializer`. Returns a context dict; the serializer iterates prefetched maps — never call back into the ORM per row. Learner-side bulk-loading lives in `courses/services/learner_service.py` (see below).

### Learner Consumption Endpoints (`/learn/...`)

Split learner surface. Endpoints, services, and serializers all live in dedicated `learner_*` modules so sensitive instructor-only fields cannot leak by accident:

| Endpoint | View | Purpose |
|---|---|---|
| `GET /learn/<slug>/curriculum/` | `LearnerCurriculumView` | Light curriculum outline: ordered sections + item rows with `title`, `item_type`, `position`. Lectures also carry `lecture_type`, `duration_seconds`, and (for learners) `is_completed`. No HLS URLs, no quiz questions, no article text. |
| `GET /learn/lectures/<int:lecture_id>/` | `LearnerLectureDetailView` | Learner-safe lecture detail. Video → HLS playlist + renditions. Article → `article_content`. Always returns the caller's `progress` (so the player can resume). |
| `POST /learn/lectures/<int:lecture_id>/progress/` | `LearnerLectureProgressView` | Idempotent upsert of `WatchProgress` via `update_or_create`. Body: `{watched_seconds, is_completed}`. Both required. The `WatchProgress` post_save signal recalculates the enrollment's `progress_percent`. |
| `GET /learn/quizzes/<int:quiz_id>/` | `LearnerQuizDetailView` | Quiz + questions + answer options (no `is_correct`) for the attempt UI. Includes the caller's `latest_attempt` summary if they've submitted before. |
| `POST /learn/quizzes/<int:quiz_id>/submit/` | `LearnerQuizSubmitView` | Creates a new `QuizAttempt` with one `QuizAttemptAnswer` per question. Returns per-question verdict — `correct_answer_id`/`correct_answer_text` appear **only when the learner got it wrong**. Each submit = new attempt row (no cap; instructor edits to the answer key don't retroactively rewrite past attempts because `is_correct` is denormalized onto the attempt). |

Permission model:
- `GET` endpoints accept *enrolled learner OR the course's own instructor* (preview matching `MyCoursesDetailView`). Slug-based curriculum → 403 for unenrolled; numeric-ID endpoints → 404 (existence not leaked).
- `POST /progress/` and `POST /submit/` are `IsLearnerUser`-gated; instructors get `403` (preview must not pollute progress or attempt history). Unenrolled learners get `404`.

Data loaders live in `courses/services/learner_service.py`:
- `resolve_course_access(user, course)` → `(is_instructor, enrollment_or_none)`. Use this in any new learner endpoint that needs the same access policy.
- `load_learner_curriculum(course, user, is_instructor)` — bulk-loads `.only(...)` lightweight fields and a single `WatchProgress` query for completion markers.
- `get_consumption_lecture(user, lecture_id)` / `get_quiz_for_consumption(user, quiz_id)` — fetch + verify access in one call. Raise `Lecture.DoesNotExist` / `Quiz.DoesNotExist` on missing-or-no-access.
- `upsert_watch_progress(user, lecture, ...)` — never touches the enrollment row directly; the signal handles recalc.
- `submit_quiz_attempt(user, quiz, answers_payload)` — atomic: creates the `QuizAttempt` + per-question `QuizAttemptAnswer` rows, computes score from the live answer key, and **caches `is_correct` onto each attempt row** so historical attempts stay frozen against later instructor edits.

Serializers live in `courses/all_serializers/learner_serializers.py`. `build_quiz_attempt_result(attempt)` is a function (not a serializer class) because the "show correct answer only when wrong" rule is awkward to express with DRF field declarations and trivial in plain Python; centralising it there means every caller (current and future) gets identical behaviour.

Phase-2 still to build: assignment consumption + submissions, coding-exercise learner runtime. When adding those, define new `Learner*Serializer` classes — do **not** reuse instructor authoring serializers, they embed sensitive fields (`solution_code`, hidden test cases, `model_answer`, `is_correct`).

`recalculate_progress()` in `enrollment_service.py` counts both lecture completion (`WatchProgress.is_completed=True`) and quiz attempts (≥1 `QuizAttempt` per quiz). Lectures recalc via the `WatchProgress` post_save signal; `submit_quiz_attempt` calls `recalculate_progress(enrollment)` directly at the end of its transaction. Assignment and coding-exercise completion are still stubs (`completed_assignments = completed_coding = 0`) — those land with their respective submission models.

`upsert_watch_progress` enforces two server-side invariants the client cannot override: `watched_seconds` is clamped to the active `VideoAsset.duration_seconds` (HLS players legitimately overshoot by a fraction, so we cap rather than reject), and if the clamped cursor lands at duration, `is_completed` is forced to `True` (the video has functionally ended regardless of what the client declared). Article lectures have no duration; `watched_seconds` is forced to `0`.

### Learner-Safe Serialization

The following fields **must remain instructor-only** in any learner-facing response:

| Field | Audience | Status |
|---|---|---|
| `Lecture.stream_master_playlist` (full HLS URL) | Exposed on `/learn/lectures/<id>/` for any caller with access; on `/catalog/` only when `Lecture.is_preview=True` | Done |
| `QuizAnswer.is_correct` | Instructor only (omit from learner payload pre-submit; score server-side; reveal the correct answer in the post-submit response only for wrong questions) | Done — `_LearnerQuizAnswerOptionSerializer` simply doesn't declare it; `build_quiz_attempt_result` controls reveal-on-wrong |
| `AssignmentQuestion.model_answer` | Instructor only | Still to build (Phase-2 assignments) |
| `CodingExerciseLanguageConfig.solution_code` | Instructor only | Still to build (Phase-2 coding) |
| `CodingTestCase.is_hidden` + hidden test cases | Instructor only (filter hidden cases out of learner payload) | Still to build (Phase-2 coding) |

Pattern: define dedicated `Learner*Serializer` classes that simply don't declare the sensitive fields. Don't rely on conditional `to_representation` stripping — a future refactor could miss a branch and leak. Absence is a stronger guarantee than conditional removal. The quiz-submission response (`build_quiz_attempt_result`) is an exception: it returns `correct_answer_*` only when `is_correct=False`, and that conditional-presence rule lives in one well-named function so the contract is easy to audit.

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

### 403 vs. 404 Access-Denied Policy

The HTTP status returned when an authenticated user lacks access to a resource is determined by the **URL identifier type**, not by personal preference. This is a cross-cutting rule — apply it uniformly when adding new endpoints.

| URL identifier | Response when caller has no access | Why |
|---|---|---|
| **Slug** (e.g. `/<slug>/`, `/my-courses/<slug>/`, `/learn/<slug>/curriculum/`) | **403** | Course slugs are public — every published course's slug appears in `/catalog/`. "You can't access this" leaks nothing new. |
| **Numeric ID** (e.g. `/lectures/<int:lecture_id>/`, `/quizzes/<int:quiz_id>/`, `/assignments/<int:assignment_id>/`, `/<int:pk>/`) | **404** | IDs are not enumerable from public surfaces. Returning 403 would confirm the resource exists, letting an attacker probe sequential IDs to map out lectures/quizzes/assignments across the platform. |

The rule applies to **both** learner consumption endpoints and instructor authoring endpoints. Examples already in the codebase:

- `LearnerCurriculumView` (slug) → 403 for unenrolled; `LearnerLectureDetailView` (lecture_id) → 404 for unenrolled.
- `MyCoursesDetailView` (slug) → 403 for unenrolled; `CourseDetailView` (int pk) → 404 for non-owning instructor.
- `AssignmentDetailAPIView`, `QuizDetailAPIView`, `CodingExerciseDetailAPIView` (all int IDs) → 404 for non-owning instructor.

Two corollaries:

1. **Don't normalize for "consistency".** Two endpoints in the same module returning different status codes is fine *if* one is slug-based and one is ID-based. The rule is consistent even when the codes differ.
2. **Don't leak existence in the error body either.** A 404 response for "no access" should use the same message ("Lecture not found.") as a true missing-row 404 — never something like "You don't have access to this lecture." That defeats the point of the 404.

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
