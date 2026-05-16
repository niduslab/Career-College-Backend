# Career College Backend

A Django REST Framework backend for a course marketplace platform. Instructors create and publish courses with mixed content (lectures, quizzes, coding exercises, assignments), upload videos that are async-transcoded to HLS, and must pass identity verification before they can author content. Learners browse and enroll in published courses.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Framework | Django 5.x + Django REST Framework 3.x |
| Auth | Simple JWT (access + refresh tokens) + django-allauth (OAuth) |
| Database | PostgreSQL (psycopg2-binary) |
| Task queue | Celery 5.x + Redis |
| Video processing | FFmpeg / FFprobe (via ffmpeg-python) |
| Media storage | Local filesystem (configurable via `MEDIA_ROOT`) |
| Production server | Gunicorn |

---

## Apps

| App | URL prefix | Responsibility |
|-----|-----------|----------------|
| `authentication` | `/api/v1/auth/` | Registration, OTP, JWT, OAuth (Google/LinkedIn), profiles |
| `courses` | `/api/v1/courses/` | Public catalog, learner enrollment, my-courses dashboard, plus instructor course authoring/curriculum |
| `id_verification` | `/api/v1/verification/` | Instructor identity verification state machine |
| `core` | — | Shared permissions, pagination, middleware |

---

## Development Setup

### Prerequisites

- Python 3.11+
- PostgreSQL running locally
- Redis running locally (required for Celery)
- FFmpeg and FFprobe installed (required for video transcoding)

### 1. Clone and create a virtual environment

```powershell
git clone <repo-url>
cd Career-College-Backend

python -m venv .venv
.\.venv\Scripts\Activate.ps1   # Windows PowerShell
# source .venv/bin/activate    # macOS / Linux
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in the required values. At minimum for local development:

```env
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

# PostgreSQL
DB_ENGINE=django.db.backends.postgresql
DB_NAME=career_college
DB_USER=postgres
DB_PASSWORD=yourpassword
DB_HOST=127.0.0.1
DB_PORT=5432

# Email (prints to terminal instead of sending)
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend

# Redis / Celery
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

# FFmpeg binaries
FFMPEG_BINARY_PATH=ffmpeg       # or absolute path, e.g. C:\tools\ffmpeg.exe
FFPROBE_BINARY_PATH=ffprobe

# JWT cookies (False for local HTTP)
JWT_COOKIE_SECURE=False
```

### 4. Create the database and run migrations

```bash
# Create the database in PostgreSQL first (psql or pgAdmin), then:
python manage.py migrate
```

### 5. Create a superuser

```bash
python manage.py createsuperuser
```

### 6. Start the development server

```bash
python manage.py runserver
```

API is now available at `http://127.0.0.1:8000/`.

### 7. Start the Celery worker (required for video transcoding)

Open a second terminal, activate the venv, then:

```bash
# Windows (solo pool avoids multiprocessing issues)
celery -A career_college_backend worker --loglevel=info --pool=solo

# macOS / Linux
celery -A career_college_backend worker --loglevel=info
```

---

## Environment Variables Reference

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `SECRET_KEY` | Yes | — | Django secret key |
| `DEBUG` | Yes | — | `True` for local dev |
| `ALLOWED_HOSTS` | Yes | — | Comma-separated, e.g. `127.0.0.1,localhost` |
| `SITE_ID` | No | `1` | Used by django-allauth |
| `DB_ENGINE` | Yes | — | e.g. `django.db.backends.postgresql` |
| `DB_NAME` | Yes | — | Database name |
| `DB_USER` | Yes | — | Database user |
| `DB_PASSWORD` | Yes | — | Database password |
| `DB_HOST` | No | `127.0.0.1` | |
| `DB_PORT` | No | `5432` | |
| `EMAIL_BACKEND` | No | SMTP | Set to `django.core.mail.backends.console.EmailBackend` for local dev |
| `EMAIL_HOST` | No | `smtp.gmail.com` | |
| `EMAIL_PORT` | No | `587` | |
| `EMAIL_USE_TLS` | No | `True` | |
| `EMAIL_HOST_USER` | Prod only | — | SMTP username |
| `EMAIL_HOST_PASSWORD` | Prod only | — | SMTP password |
| `DEFAULT_FROM_EMAIL` | Prod only | — | Sender address |
| `OTP_RATE_LIMIT` | No | — | e.g. `20/min` |
| `CELERY_BROKER_URL` | Yes | `redis://127.0.0.1:6379/0` | Redis URL |
| `CELERY_RESULT_BACKEND` | No | same as broker | |
| `MEDIA_URL` | No | `/media/` | |
| `MEDIA_ROOT` | No | `media/` | Absolute or relative path |
| `FFMPEG_BINARY_PATH` | Yes (video) | `ffmpeg` | Absolute path or command name if in PATH |
| `FFPROBE_BINARY_PATH` | Yes (video) | `ffprobe` | |
| `JWT_COOKIE_SECURE` | No | `not DEBUG` | Set `False` for local HTTP |
| `JWT_COOKIE_SAMESITE` | No | `Lax` | |
| `GOOGLE_CLIENT_ID` | OAuth only | — | Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | OAuth only | — | |
| `GOOGLE_CALLBACK_URL` | OAuth only | — | Backend callback URL |
| `LINKEDIN_CLIENT_ID` | OAuth only | — | LinkedIn Developer portal |
| `LINKEDIN_CLIENT_SECRET` | OAuth only | — | |
| `LINKEDIN_CALLBACK_URL` | OAuth only | — | Backend callback URL |
| `FRONTEND_URL` | No | `http://localhost:3000` | |
| `FRONTEND_GOOGLE_CALLBACK` | OAuth only | — | Frontend redirect after Google OAuth |
| `FRONTEND_LINKEDIN_CALLBACK` | OAuth only | — | Frontend redirect after LinkedIn OAuth |
| `FRONTEND_ERROR_URL` | OAuth only | — | Frontend error redirect |

---

## Key Workflows

### 1. Instructor onboarding

1. `POST /api/v1/auth/register/` — register with email + password; OTP sent by email.
2. `POST /api/v1/auth/otp/verify/` — verify OTP; sets `is_email_verified = True`.
3. `POST /api/v1/auth/login/` — receive `access` (12 h) and `refresh` (7 days) JWT tokens.
4. `POST /api/v1/verification/create/` — create identity verification draft.
5. `PATCH /api/v1/verification/{id}/update/` — upload identity documents.
6. `POST /api/v1/verification/{id}/submit/` — submit for admin review.
7. Admin approves via `POST /api/v1/verification/admin/{id}/review/` → `InstructorProfile.is_verified = True`.
8. All course authoring endpoints are now accessible.

### 2. Course creation and publication

1. `POST /api/v1/courses/create/` — create course (title, description, category, level, price). Status starts as `draft`.
2. Add metadata: learning objectives, prerequisites, audience entries.
3. `POST /api/v1/courses/{id}/sections/create/` — add one or more sections.
4. Add content to each section via `POST /api/v1/courses/sections/{section_id}/contents/` with `item_type: lecture | quiz | coding | assignment`.
5. For video lectures: upload file, Celery transcodes to HLS; poll until `active_video_asset.status == ready`.
6. `POST /api/v1/courses/{id}/submit/` — completeness checks run; status moves to `under_review`.
7. Admin reviews: `POST /api/v1/courses/{id}/review/` with `{"action": "approve"}` → `published`; or `{"action": "reject", "rejection_reason": "..."}` → `rejected`.
8. If rejected, instructor calls `POST /api/v1/courses/{id}/rework/` → back to `draft` for fixes.

### 3. Adding curriculum content

All content is created through one endpoint:

```
POST /api/v1/courses/sections/{section_id}/contents/
```

The `item_type` field determines what is created:

| `item_type` | Creates | Key extra fields |
|-------------|---------|-----------------|
| `lecture` | `Lecture` + `SectionContent` | `lecture_type` (article/video), `article_content` or `video_file` |
| `quiz` | `Quiz` + `SectionContent` | `title`, `description` |
| `coding` | `CodingExercise` + `SectionContent` | `difficulty`, `default_language`, `supported_languages` |
| `assignment` | `Assignment` + `SectionContent` | `instructions`, `passing_score` |

The returned `content_id` (`SectionContent.id`) is used for reordering:

```
PATCH /api/v1/courses/contents/{content_id}/reorder/   body: {"position": N}
```

### 4. Video transcoding pipeline

1. Create a video lecture via `sections/{id}/contents/` with `lecture_type: video` and `video_file`.
2. Backend creates a `VideoAsset` with `status: uploading` and enqueues a Celery task.
3. Worker runs FFmpeg → produces 5 HLS renditions (240p, 360p, 480p, 720p, 1080p).
4. `VideoAsset.status` transitions: `uploading → processing → ready` (or `failed`; auto-retries ×3).
5. On ready: `Lecture.stream_master_playlist` and `stream_renditions` are populated.
6. Poll `GET /api/v1/courses/lectures/{lecture_id}/` and check `active_video_asset.status`.

### 5. Course status state machine

```
                    submit               approve
  draft  ─────────────────►  under_review  ──────────►  published  ──►  archived
    ▲                               │                                       │
    │                        reject │                                       │
    │                               ▼                                       │
    └─────────────────────  rejected                        draft  ◄────────┘
           rework
```

| Endpoint | Who | Transition |
|----------|-----|-----------|
| `POST {id}/submit/` | Verified instructor (on course) | `draft → under_review` |
| `POST {id}/review/` action=approve | Admin | `under_review → published` |
| `POST {id}/review/` action=reject | Admin | `under_review → rejected` |
| `POST {id}/rework/` | Verified instructor (on course) | `rejected → draft` |
| `POST {id}/archive/` | Instructor or Admin | `published → archived` |

Submit runs a completeness check: title + description present, at least one section, every section has content, all videos are `ready`, every quiz has questions with at least one correct answer each.

### 6. Learner enrollment and access

1. Public users browse `GET /api/v1/courses/catalog/` and `GET /api/v1/courses/catalog/{slug}/`.
2. Authenticated learners enroll via `POST /api/v1/courses/{slug}/enroll/`.
3. Enrollments are unique per learner+course; re-enrolling reactivates the existing record.
4. Learners can view progress via `GET /api/v1/courses/my-courses/` and `GET /api/v1/courses/my-courses/{slug}/`.
5. Learners can soft-unenroll via `POST /api/v1/courses/{slug}/unenroll/`; progress stays preserved.

---

## API Endpoints

Base URL: `http://127.0.0.1:8000`

All authenticated endpoints require: `Authorization: Bearer <access_token>`

### Authentication — `/api/v1/auth/`

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `register/` | No | Register with email + password |
| POST | `login/` | No | Email/password login; returns JWT tokens |
| POST | `token/refresh/` | No | Refresh access token |
| POST | `logout/` | Yes | Blacklist refresh token |
| POST | `otp/verify/` | No | Verify OTP sent after registration |
| POST | `otp/resend/` | No | Resend OTP |
| POST | `password/forgot/` | No | Send password reset OTP |
| POST | `password/reset/` | No | Reset password with OTP |
| POST | `password/change/` | Yes | Change password (authenticated) |
| GET/PATCH | `profile/me/` | Yes | View or update own profile |
| GET/POST | `profile/me/education/` | Yes | Education history |
| GET/PATCH/DELETE | `profile/me/education/{id}/` | Yes | Education entry detail |
| GET/POST | `profile/me/work-experience/` | Yes | Work experience history |
| GET/PATCH/DELETE | `profile/me/work-experience/{id}/` | Yes | Work experience detail |
| GET | `profiles/learners/` | No | Public learner list |
| GET | `profiles/instructors/` | No | Public instructor list |
| GET | `profiles/institutions/` | No | Public institution list |
| GET | `profiles/{slug}/` | No | Public profile by slug |

#### OAuth

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `google/` | Redirect to Google consent screen |
| GET | `google/callback/` | Receive authorization code from Google |
| POST | `google/exchange-token/` | Exchange code for local JWT session |
| GET | `linkedin/` | Redirect to LinkedIn consent screen |
| GET | `linkedin/callback/` | Receive authorization code from LinkedIn |
| POST | `linkedin/exchange-token/` | Exchange code for local JWT session |

---

### Identity Verification — `/api/v1/verification/`

#### Instructor

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `create/` | Create a verification draft |
| PATCH | `{id}/update/` | Upload identity documents |
| POST | `{id}/submit/` | Submit for admin review |
| GET | `my/` | List own verification submissions |
| GET | `my/{id}/` | Detail view of own submission |

#### Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `admin/list/` | List all submissions |
| GET | `admin/{id}/` | Detail view |
| POST | `admin/{id}/review/` | Approve, reject, or request action |

---

### Courses — `/api/v1/courses/`

#### Public Catalog

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `catalog/` | List published courses (public) |
| GET | `catalog/{slug}/` | Published course detail (public) |

#### Learner Enrollment and Dashboard

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `{slug}/enroll/` | Learner | Enroll in a published course (or reactivate prior enrollment) |
| POST | `{slug}/unenroll/` | Learner | Soft-unenroll while preserving progress |
| GET | `my-courses/` | Learner | List active enrollments with progress |
| GET | `my-courses/{slug}/` | Learner | Enrollment detail for a single course |

#### Course CRUD

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `` (root) | List own courses (paginated) |
| POST | `create/` | Create a new course (starts as `draft`) |
| GET | `{id}/` | Course detail |
| PATCH | `{id}/` | Update course fields (not status) |
| DELETE | `{id}/` | Delete course |

#### Course Status Transitions

| Method | Endpoint | Who | Description |
|--------|----------|-----|-------------|
| POST | `{id}/submit/` | Instructor | Submit draft for review |
| POST | `{id}/review/` | Admin | Approve (`action: approve`) or reject (`action: reject` + `rejection_reason`) |
| POST | `{id}/rework/` | Instructor | Move rejected course back to draft |
| POST | `{id}/archive/` | Instructor or Admin | Archive a published course |
| POST | `{id}/restore/` | Instructor or Admin | Restore an archived course back to draft |

##### Expected Responses (Status Lifecycle)

`POST {id}/submit/`
- `200 OK` on successful `draft -> under_review`
- `400 Bad Request` for completeness/field validation failures (`errors` payload)
- `422 Unprocessable Entity` for invalid transition/business-rule violations

`POST {id}/review/`
- `200 OK` on successful `under_review -> published` or `under_review -> rejected`
- `400 Bad Request` for invalid action/rejection payload validation (`errors` payload)
- `422 Unprocessable Entity` for invalid transition/business-rule violations

`POST {id}/rework/`
- `200 OK` on successful `rejected -> draft`
- `400 Bad Request` for payload/field validation failures (`errors` payload)
- `422 Unprocessable Entity` for invalid transition/business-rule violations

`POST {id}/archive/`
- `200 OK` on successful `published -> archived`
- `400 Bad Request` for payload/field validation failures (`errors` payload)
- `422 Unprocessable Entity` for invalid transition/business-rule violations

`POST {id}/restore/`
- `200 OK` on successful `archived -> draft`
- `400 Bad Request` for payload/field validation failures (`errors` payload)
- `422 Unprocessable Entity` for invalid transition/business-rule violations

#### Course Metadata

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `{id}/learning-objectives/` | List or add learning objectives |
| GET/PATCH/DELETE | `learning-objectives/{item_id}/` | Detail |
| GET/POST | `{id}/prerequisites/` | List or add prerequisites |
| GET/PATCH/DELETE | `prerequisites/{item_id}/` | Detail |
| GET/POST | `{id}/audiences/` | List or add audience entries |
| GET/PATCH/DELETE | `audiences/{item_id}/` | Detail |

#### Sections

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `{id}/sections/` | List sections |
| POST | `{id}/sections/create/` | Create a section |
| GET/PATCH/PUT/DELETE | `sections/{section_id}/` | Section detail |

#### Curriculum (all content types)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `sections/{section_id}/contents/` | List or create any content item |
| PATCH | `contents/{content_id}/reorder/` | Reorder an item within its section |

#### Lectures

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `sections/{section_id}/lectures/` | List lectures in a section |
| GET/PATCH/PUT/DELETE | `lectures/{lecture_id}/` | Lecture detail |

#### Quizzes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/PATCH/DELETE | `quizzes/{quiz_id}/` | Quiz detail |
| GET/POST | `quizzes/{quiz_id}/questions/` | List or add questions |
| GET/PATCH/DELETE | `quiz-questions/{question_id}/` | Question detail |
| GET/POST | `quiz-questions/{question_id}/answers/` | List or add answers |
| GET/PATCH/DELETE | `quiz-answers/{answer_id}/` | Answer detail |

#### Assignments

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `sections/{section_id}/assignments/` | List assignments in a section |
| GET/PATCH/DELETE | `assignments/{assignment_id}/` | Assignment detail |
| GET/POST | `assignments/{assignment_id}/questions/` | List or add questions |
| PATCH | `assignments/{assignment_id}/questions/reorder/` | Reorder questions |
| GET/PATCH/DELETE | `assignment-questions/{question_id}/` | Question detail |

#### Coding Exercises

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/PATCH/DELETE | `coding-exercises/{exercise_id}/` | Exercise detail |
| GET/POST | `coding-exercises/{exercise_id}/language-configs/` | List or add language configs |
| GET/PATCH/DELETE | `coding-exercises/{exercise_id}/language-configs/{config_id}/` | Config detail |
| GET/POST | `coding-exercises/{exercise_id}/testcases/` | List or add test cases |
| GET/PATCH/DELETE | `coding-exercises/{exercise_id}/testcases/{tc_id}/` | Test case detail |

---

## Response Format

All endpoints return the same envelope:

```json
// Success
{ "success": true, "message": "...", "data": { ... } }

// Paginated list
{ "success": true, "data": { "count": 42, "next": "...", "previous": "...", "results": [...] } }

// Validation error (400)
{ "success": false, "message": "Validation failed.", "errors": { "field": ["..."] } }

// Not found (404)
{ "success": false, "message": "Course not found." }

// Business logic violation (422)
{ "success": false, "message": "Cannot transition from \"draft\" to \"published\"." }

// Server error (500)
{ "success": false, "message": "An unexpected error occurred. Please try again." }
```

---

## Useful Commands

```bash
# Database
python manage.py migrate
python manage.py makemigrations
python manage.py createsuperuser

# Development server
python manage.py runserver

# Tests
python manage.py test                       # all apps
python manage.py test courses               # single app
python manage.py test authentication.tests  # single module

# Django system checks
python manage.py check

# Celery worker
celery -A career_college_backend worker --loglevel=info --pool=solo  # Windows
celery -A career_college_backend worker --loglevel=info              # macOS/Linux

# Seed course categories
python manage.py seed_course_categories
python manage.py seed_course_categories --dry-run

# Content repair utilities
python manage.py populate_section_content --dry-run
python manage.py reindex_section_content_positions --dry-run
```

---

## Project Structure

```
career_college_backend/
├── career_college_backend/        Django project config (settings, root urls, celery)
├── authentication/                Auth app — registration, OTP, JWT, OAuth, profiles
│   ├── all_views/
│   │   ├── auth_views.py          Register, login, logout, token refresh
│   │   ├── otp_views.py           OTP verify and resend
│   │   ├── password_views.py      Forgot, reset, change password
│   │   ├── profile_views.py       Profile me + public profile endpoints
│   │   ├── google_views.py        Google OAuth flow
│   │   └── linkedin_views.py      LinkedIn OAuth flow
│   ├── models.py                  User, LearnerProfile, InstructorProfile, PartnerInstitutionProfile
│   ├── serializers.py
│   ├── signals.py                 Auto-create profile on user creation
│   └── services/                  OAuth provisioning helpers
├── courses/                       Course and enrollment app
│   ├── all_views/
│   │   ├── course_views.py        Course list / create / detail
│   │   ├── status_views.py        Submit / review / rework / archive
│   │   ├── content_views.py       Sections, SectionContent, lectures, quizzes
│   │   ├── assignment_views.py    Assignments and questions
│   │   ├── coding_views.py        Coding exercises, language configs, test cases
│   │   └── enrollment_views.py    Public catalog, enroll/unenroll, my-courses dashboard
│   ├── all_models/                Course and enrollment domain models
│   ├── all_serializers/           Modular serializers by feature
│   ├── services/                  Curriculum, video, and enrollment service helpers
│   ├── selectors.py               Reusable query helpers
│   ├── signals.py                 Domain event hooks (enrollment progress updates)
│   ├── tasks.py                   Celery tasks (video transcoding)
│   ├── transcoding.py             FFmpeg transcoding routines
│   └── management/commands/
│       └── seed_course_categories.py
├── id_verification/               Identity verification workflow
├── core/                          Shared: permissions, pagination, middleware
├── docs/architecture/             13 architecture design documents
├── templates/                     Email templates
├── manage.py
├── requirements.txt
├── .env.example
├── CLAUDE.md
├── POSTMAN_TESTING_GUIDE.md
├── COURSES_API_TESTING_GUIDE.md
└── FRONTEND_ERROR_RESPONSE_FORMAT.md
```

---

## Architecture Notes

- **Custom user model** — `authentication.User`, email-based (no username field). `user_type` field: `learner`, `instructor`, `partner_institution`, `admin`.
- **SectionContent** — single source of truth for curriculum ordering within a section. Holds a `GenericForeignKey` to `Lecture`, `Quiz`, `CodingExercise`, or `Assignment`. Deleting any content object cascades and removes its `SectionContent` slot.
- **Status transitions** — `NidusCourse.transition_to()` is the only entry point for status changes. Never set `status` directly on a course instance.
- **Video pipeline** — raw upload → Celery → FFmpeg → 5 HLS renditions. `VideoAsset.status` tracks progress. Only one active asset per lecture at a time.
- **Identity verification** — instructors must have `InstructorProfile.is_verified = True` before any course authoring endpoint is accessible (`IsVerifiedInstructor` permission).
- **Enrollment** — `Enrollment` enforces one record per learner/course, supports soft-unenroll (`is_active=False`), and stores denormalized `progress_percent` for dashboard performance.
- **Permissions** — all custom permission classes live in `core/permissions.py`. Never define them inside app directories. `IsLearnerUser` gates learner-only enrollment/dashboard endpoints.
- **`solution_code`** on `CodingExerciseLanguageConfig` and **`model_answer`** on `AssignmentQuestion` are instructor-only and must never appear in learner-facing serializers.
- **Hidden test cases** (`CodingTestCase.is_hidden = True`) are for grading only and must never be returned to learners.

---

## Documentation

| File | Contents |
|------|----------|
| [POSTMAN_TESTING_GUIDE.md](POSTMAN_TESTING_GUIDE.md) | Auth and profile API testing |
| [COURSES_API_TESTING_GUIDE.md](COURSES_API_TESTING_GUIDE.md) | Courses, quizzes, coding exercises, assignments API testing |
| [FRONTEND_ERROR_RESPONSE_FORMAT.md](FRONTEND_ERROR_RESPONSE_FORMAT.md) | Error response shape spec |
| [docs/architecture/](docs/architecture/) | 13 architecture design documents |
| [CLAUDE.md](CLAUDE.md) | AI assistant coding instructions |
