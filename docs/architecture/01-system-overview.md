# 01) System Overview

## Project layout

- `career_college_backend/`
  - `settings.py`: project configuration, logging, DRF/JWT settings
  - `urls.py`: root router
- `auth/`: registration/login/OAuth/OTP/password/profile APIs
- `courses/`: course, sections, mixed curriculum, lectures, quizzes, coding exercises
  - `all_views/course_views.py`: course list/create/detail
  - `all_views/content_views.py`: sections, SectionContent, lectures, quizzes, questions, answers
  - `all_views/coding_views.py`: coding exercises, language configs, test cases
  - `services.py`: curriculum ordering, video pipeline helpers
  - `selectors.py`: reusable query helpers
  - `tasks.py`: Celery async tasks (video transcoding)
  - `transcoding.py`: FFmpeg transcoding routines
- `id_verification/`: instructor identity-verification workflow
- `core/`: shared permissions, pagination, middleware

## API route map

Defined in `career_college_backend/urls.py`:

- `/api/v1/auth/` -> `auth.urls`
- `/api/v1/verification/` -> `id_verification.urls`
- `/api/v1/courses/` -> `courses.urls`

## Design patterns used

- Class-based DRF `APIView` — every view is an explicit subclass with manual `get`, `post`, `patch`, `delete` methods. No generic views, no ViewSets.
- App-level `views.py` as export surface; real view implementations live in `all_views/`
- Serializers handle shape and field-level validation only; no business logic inside them
- Services/selectors in `courses/` for business logic and query logic
- `SectionContent` GenericForeignKey for mixed-type curriculum ordering — lectures, quizzes, and coding exercises all slot into the same ordering layer

## Important conventions

- Instructor ownership checks are enforced in view queryset filters (`section__course__instructors=request.user`), not in separate permission classes.
- `SectionContent.position` is the single source of truth for item order within a section.
- Lecture, Quiz, and CodingExercise are domain objects; their placement in the curriculum is tracked separately via `SectionContent`.
- All permission classes live in `core/permissions.py`. No permissions are defined inside app directories.
- All responses follow the `{ "success": true/false, ... }` envelope. See `FRONTEND_ERROR_RESPONSE_FORMAT.md` for the full shape.

## Request lifecycle

1. Request enters from project router (`career_college_backend/urls.py`).
2. Request is routed to app-level `urls.py`.
3. View validates auth/permissions and parses request.
4. Serializer validates payload and shapes data.
5. Service/selector/model layer executes domain logic and DB operations.
6. API returns normalized response payload.

## System Explanation (Why This Design)

- Clear URL layering makes large API surfaces manageable.
- View/serializer/service separation keeps code easier to test and maintain.
- Shared utilities in `core/` reduce duplication across apps.
- Explicit `APIView` subclasses make HTTP method intent obvious and avoid the hidden magic of generic views.
