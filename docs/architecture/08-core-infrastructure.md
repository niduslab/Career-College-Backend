# 08) Core Infrastructure

Shared framework code lives in `core/`.

## Key files

- `core/permissions.py`
- `core/pagination.py`
- `core/middleware.py`
- `career_college_backend/settings.py`

## Permissions

From `core/permissions.py`:

- `IsEmailVerified`: user must be authenticated and email-verified.
- `IsInstructorUser`: user must be instructor type.
- `IsVerifiedInstructor`: instructor must have verified instructor profile.
- `IsCourseInstructor`: object-level check for course instructor membership.
- `IsAdminOrReadOnly`, `IsProfileOwner`: shared generic permissions.

Used heavily in course and profile APIs.

## Pagination

- `core/pagination.py` exposes standard DRF pagination class(es).
- Course listing endpoints use this for paginated responses.

## Middleware

- `core/middleware.py` contains custom middleware (if enabled in settings).
- Check `settings.py` `MIDDLEWARE` list for active usage.

## Logging

`settings.py` includes a safe startup fallback:

- Attempts file logging to `logs/app.log`.
- If file is not writable, runtime falls back to console handlers.

## Async and background work

- Celery settings are configured in `settings.py`.
- Course video processing uses task + service + transcoding modules in `courses/`.

## New developer checklist

1. Run migrations.
2. Create admin/superuser.
3. Seed sample course/section/content data.
4. Read docs in `docs/onboarding/` order.
5. Start by tracing one endpoint end-to-end:
   - URL -> view -> serializer -> service -> model.

## Workflow

1. Shared permission/pagination/middleware utilities are imported by app views.
2. Settings configure auth, logging, celery, and middleware behavior globally.
3. App modules compose shared utilities into feature endpoints.
4. Runtime logging/infra behavior stays centralized in project settings.

## System Explanation (Why This Design)

- Centralized infrastructure avoids per-app drift and inconsistent behavior.
- Shared permissions enforce a single policy source across APIs.
- Safe logging fallback improves reliability across local/dev environments.
