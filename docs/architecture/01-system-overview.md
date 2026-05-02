# 01) System Overview

## Project layout

- `career_college_backend/`
  - `settings.py`: project configuration, logging, DRF/JWT settings
  - `urls.py`: root router
- `auth/`: registration/login/OAuth/OTP/password/profile APIs
- `courses/`: course, sections, mixed curriculum, lectures, quizzes
- `id_verification/`: instructor identity-verification workflow
- `core/`: shared permissions, pagination, middleware

## API route map

Defined in `career_college_backend/urls.py`:

- `/api/v1/auth/` -> `auth.urls`
- `/api/v1/verification/` -> `id_verification.urls`
- `/api/v1/courses/` -> `courses.urls`

## Design patterns used

- Class-based DRF `APIView`
- App-level `views.py` as export surface, real views in `all_views/`
- Serializers for validation and persistence
- Services/selectors in `courses/` for business logic and query logic
- Generic relation (`SectionContent`) for mixed curriculum ordering

## Important conventions

- Instructor ownership checks are done in view queryset filters.
- `SectionContent.position` is the source of truth for section item order.
- Quiz and lecture are domain objects; their placement in curriculum is separate.
- Many responses use wrapper style:
  - `{ "success": true/false, ... }`

## Workflow

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
