# 08) Core Infrastructure

Shared framework code that all apps depend on. Nothing in `core/` is app-specific — anything
shared across two or more apps belongs here.

## Key files

| File | Purpose |
|------|---------|
| `core/permissions.py` | All custom DRF permission classes |
| `core/pagination.py` | `StandardResultsSetPagination` |
| `core/middleware.py` | Custom middleware |
| `career_college_backend/settings.py` | Global project settings: DB, JWT, Celery, logging, DRF |

---

## Permissions (`core/permissions.py`)

All permission classes live here. **Never define permissions inside individual app directories.**
If a permission is specific to one app today but could plausibly guard another resource tomorrow,
it still belongs in `core/`.

### Permission class reference

| Class | What it checks | Example endpoints |
|-------|---------------|-------------------|
| `IsPlatformAdmin` | `user.is_staff == True` OR `user.user_type == 'admin'` | Admin review endpoints, admin verification list |
| `IsEmailVerified` | `user.is_email_verified == True` | Almost all authenticated endpoints |
| `IsInstructorUser` | `user.user_type == 'instructor'` | Instructor-only reads (profile, verification listing) |
| `IsVerifiedInstructor` | `user.user_type == 'instructor'` AND `InstructorProfile.is_verified == True` | Course create, course authoring endpoints |
| `IsCourseInstructor` | Object-level: `user in course.instructors.all()` | Course detail/edit for multi-instructor courses |
| `IsLearnerUser` | `user.user_type == 'learner'` | Enrollment writes, progress POST, quiz submit, assignment submit |

**Usage pattern in views:**

```python
class CourseCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedInstructor]
```

Permission classes are evaluated in order. The first failure raises a `PermissionDenied` (403)
or `NotAuthenticated` (401) response immediately.

**Ownership checks are NOT permission classes.** Instructor ownership of a course is enforced via
queryset filters:
```python
course = NidusCourse.objects.get(pk=pk, instructors=request.user)
# → raises DoesNotExist → view returns 404 (not 403)
```

This follows the project's 403-vs-404 policy: numeric-ID URLs return 404 on access denial;
slug-based URLs return 403. See `CLAUDE.md` for the full policy.

---

## Pagination (`core/pagination.py`)

**`StandardResultsSetPagination`:**
- Default page size: 10
- Max page size: 100
- Configurable via `?page_size=N` query param
- Used by all list endpoints that return paginated results

**Response shape:**

```json
{
  "success": true,
  "data": {
    "count": 42,
    "next": "http://localhost:8000/api/v1/courses/?page=3",
    "previous": "http://localhost:8000/api/v1/courses/?page=1",
    "results": [...]
  }
}
```

**Usage pattern in views:**

```python
from core.pagination import StandardResultsSetPagination

def get(self, request):
    queryset = NidusCourse.objects.filter(...)
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = NidusCourseSerializer(page, many=True)
    paginated_response = paginator.get_paginated_response(serializer.data)
    paginated_response.data = {'success': True, 'data': paginated_response.data}
    return paginated_response
```

---

## Middleware (`core/middleware.py`)

Custom middleware registered in `settings.py`'s `MIDDLEWARE` list. Check `settings.py` for which
middleware is currently active and its position in the stack.

---

## JWT configuration (`settings.py`)

```python
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME':  timedelta(hours=12),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS':  True,    # new refresh token issued on each refresh
    'BLACKLIST_AFTER_ROTATION': True,  # old refresh token blacklisted immediately
    'AUTH_HEADER_TYPES': ('Bearer',),
}
```

Token delivery: JSON response body **and** optionally HttpOnly cookies. Cookie helpers are in
`authentication/utils/cookie_helpers.py`. Set `JWT_COOKIE_SECURE=False` in `.env` for local
HTTP development.

---

## Celery configuration (`settings.py`)

```python
CELERY_BROKER_URL      = env('CELERY_BROKER_URL')   # e.g. redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND  = env('CELERY_BROKER_URL')   # same Redis instance
CELERY_RESULT_EXPIRES  = 3600                        # 1-hour TTL for task results
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT  = ['json']
```

**Tasks registered in `courses/tasks.py`:**

| Task | Trigger | Behavior |
|------|---------|---------|
| `transcode_video_asset_task` | `replace_lecture_video_and_enqueue_transcoding()` | Runs FFmpeg, produces 5 HLS renditions; 3 retries with exponential backoff |
| `grade_assignment_submission_task` | `submit_assignment()` via `transaction.on_commit` | Runs `RubricGrader`; transitions submission to `passed/failed/grading_failed`; 3 retries |
| `evaluate_coding_run_task` | `run_coding_exercise()` | Runs Docker sandbox on visible tests; no DB write; result in Celery backend (1-hour TTL) |
| `evaluate_coding_submission_task` | `submit_coding_exercise()` via `transaction.on_commit` | Runs Docker sandbox on all tests; writes `CodingSubmissionTestResult` rows; 3 retries on `DockerTransientError` |
| `reap_stuck_coding_submissions_task` | Celery beat (every 60s) | Flips stale `queued/grading` submissions to `error` after 5 minutes — prevents UI hangs from crashed workers |

**Starting a Celery worker:**
```bash
celery -A career_college_backend worker -l info
```

**Celery beat (for reaper task):**
```bash
celery -A career_college_backend beat -l info
```

---

## Logging configuration (`settings.py`)

```python
LOGGING = {
    'version': 1,
    'handlers': {
        'file': {
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs' / 'app.log',
            ...
        },
        'console': {'class': 'logging.StreamHandler'},
    },
    ...
}
```

**Safe startup fallback:** On startup, the settings module attempts to create `logs/app.log`.
If the directory is not writable (common in Docker containers or CI), logging automatically
falls back to the console handler. No crash, no manual setup needed.

**Usage in views/services:**
```python
import logging
logger = logging.getLogger(__name__)

# In exception handlers:
logger.error(f"Video transcoding failed for asset {video_asset.id}: {e}")
logger.exception("Unexpected error during quiz submission")  # includes traceback
```

---

## DRF configuration (`settings.py`)

```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'core.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 10,
}
```

Views override `permission_classes` explicitly — the default `IsAuthenticated` is the safety net
for any view that forgets to declare permissions, not the primary guard.

---

## New developer checklist

1. Copy `.env.example` to `.env` and fill in all values (DB credentials, FFmpeg paths, Redis URL, etc.)
2. Run `python manage.py migrate`
3. Run `python manage.py createsuperuser`
4. Start the dev server: `python manage.py runserver`
5. Start a Celery worker: `celery -A career_college_backend worker -l info`
6. Verify the setup by tracing one endpoint end-to-end:
   `URL → app urls.py → view → permission checks → serializer → service → model → response`
7. Read `docs/architecture/` in order (01 through 13) before making structural changes.

---

## Why this design

- **Centralized `core/`** avoids per-app permission drift — a change to `IsVerifiedInstructor`
  takes effect across all apps immediately.
- **Shared pagination** ensures every list endpoint has the same response shape and supports
  the same `?page_size=N` query param.
- **Safe logging fallback** avoids startup crashes in environments where the log directory does
  not exist (Docker, CI, fresh checkouts).
- **`transaction.on_commit` for Celery task dispatch** prevents phantom tasks from being enqueued
  if the surrounding DB transaction is rolled back — the task only fires if the commit succeeds.
