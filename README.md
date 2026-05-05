# Career College Backend

Backend API built with Django and Django REST Framework.

## Tech Stack

- Python 3.14+
- Django 5.x
- Django REST Framework
- Simple JWT
- Celery (background processing)
- FFmpeg/FFprobe (video transcoding pipeline)

## Apps

- `auth`: registration, login, OTP, password, OAuth, profile APIs
- `courses`: course authoring, curriculum, lectures, quizzes, coding exercises
- `id_verification`: instructor identity verification workflow
- `core`: shared permissions, pagination, middleware

## Base API URLs

- Auth: `http://127.0.0.1:8000/api/v1/auth/`
- Verification: `http://127.0.0.1:8000/api/v1/verification/`
- Courses: `http://127.0.0.1:8000/api/v1/courses/`

## Quick Start

```bash
# create venv
python -m venv .venv

# activate (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# install deps
python -m pip install -r requirements.txt

# migrate
python manage.py migrate

# run server
python manage.py runserver
```

## Environment Setup

1. Create local env file:

```bash
cp .env.example .env
```

2. Set required values in `.env`:

- `SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `DB_ENGINE`
- `DB_NAME`
- `EMAIL_*`
- `DEFAULT_FROM_EMAIL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `FFMPEG_BINARY_PATH`
- `FFPROBE_BINARY_PATH`
- `LOG_DIR` (optional)

3. Local email testing option:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

## Core Features

### Auth & Security

- Custom email-based user model (no username — email is identity)
- OTP verification on registration and password reset
- JWT auth with 12h access token + 7-day rotating refresh token
- Google and LinkedIn OAuth (authorization-code flow)
- Role-aware permissions: learner, instructor, partner institution, admin

### Profiles

- Separate profile models per user type (`LearnerProfile`, `InstructorProfile`, `PartnerInstitutionProfile`)
- Education and work experience history per user
- Public profile browsing endpoints with slug-based lookup

### Instructor Verification

- State machine: `draft → submitted → under_review → approved / rejected / action_required`
- Instructors upload identity documents; admins transition state
- Approval automatically sets `InstructorProfile.is_verified = True`
- `IsVerifiedInstructor` permission gates all course authoring endpoints

### Course Authoring

- Course with category, metadata (objectives, prerequisites, audience), thumbnail, pricing, level
- Sections within a course, each with ordered curriculum items
- Mixed content ordering via `SectionContent` — single source of truth for all item positions
- Reorder API shifts neighboring items atomically; no gaps left after deletion

### Lecture / Video

- Two lecture types: `article` (rich text) and `video` (uploaded file)
- Video upload triggers async Celery task that transcodes to 5 HLS renditions (240p–1080p)
- `VideoAsset` tracks file state; `VideoProcessingJob` tracks transcoding status
- Only one active asset per lecture at any time

### Quizzes

- Practice-oriented: no passing score, no time limit
- Questions are ordered within a quiz; answers enforce exactly one correct option per question
- Can be created curriculum-first (through section contents) or directly (then placed via `SectionContent`)

### Coding Exercises (Part 1 — CRUD)

- Coding problem attached to a section; positioned in curriculum via `SectionContent`
- Difficulty levels: easy, medium, hard
- Per-language starter and solution code via `CodingExerciseLanguageConfig` (instructor-only; solution never exposed to learners)
- Ordered test cases via `CodingTestCase`; hidden cases are grading-only
- Position sequence stays contiguous after test case deletion (no gaps)

## How the System Works: Key Workflows

These step-by-step sequences explain the main feature flows from first request to final database state.

### 1. Instructor onboarding flow

1. Register at `POST /api/v1/auth/register/` → OTP sent by email.
2. Verify OTP at `POST /api/v1/auth/otp/verify/` → `is_email_verified = True`.
3. Log in at `POST /api/v1/auth/login/` → receive `access` and `refresh` JWT tokens.
4. Create an identity verification draft at `POST /api/v1/verification/create/`, upload documents, submit at `POST /api/v1/verification/{id}/submit/`.
5. Admin reviews and approves → `InstructorProfile.is_verified = True`.
6. All course-authoring endpoints (`IsVerifiedInstructor`) are now accessible.

### 2. Course creation flow

1. `POST /api/v1/courses/create/` — create course with title, description, category, level, price.
2. Add learning objectives: `POST /api/v1/courses/{course_id}/learning-objectives/`.
3. Add prerequisites and audience entries through matching endpoints.
4. Create sections: `POST /api/v1/courses/{course_id}/sections/create/` — each gets a `position`.
5. Add curriculum items to each section (see next workflow).
6. When ready, transition course `status` to `under_review` for admin publishing.

### 3. Adding curriculum content to a section

All curriculum items go through `POST /api/v1/courses/sections/{section_id}/contents/`.
The `item_type` field determines what gets created:

- `item_type: "lecture"` → creates a `Lecture` + `SectionContent` row in one transaction.
- `item_type: "quiz"` → creates a `Quiz` + `SectionContent` row.
- `item_type: "coding"` → creates a `CodingExercise` + `SectionContent` row.

The returned `content_id` is the `SectionContent` ID used for reordering.
To reorder, `PATCH /api/v1/courses/contents/{content_id}/reorder/` with the new `position`.
The service layer locks affected rows with `SELECT FOR UPDATE` and shifts neighbors atomically.

### 4. Video upload and transcoding flow

1. Create a video lecture (via contents or lecture endpoint) → `Lecture` row created.
2. `PATCH /api/v1/courses/lectures/{lecture_id}/` with `video_file` multipart field.
3. Backend deactivates the previous `VideoAsset` (if any), creates a new active one, enqueues a Celery task.
4. Celery worker picks up `transcode_video_asset_task`, calls FFmpeg to produce 5 HLS renditions.
5. On completion, `VideoAsset.status` → `ready`; `Lecture.stream_master_playlist` and `stream_renditions` are populated.
6. On failure, `VideoAsset.status` → `failed`; task auto-retries up to 3 times with backoff.

### 5. Coding exercise authoring flow

1. Add the exercise to a section via `POST /sections/{section_id}/contents/` with `item_type: "coding"`.
   - Response includes `exercise_id` (from `SectionContent.object_id`).
2. Add per-language configurations: `POST /coding-exercises/{exercise_id}/language-configs/`
   - Supply `language`, `starter_code`, and `solution_code`.
   - One config per language; duplicate language returns 400.
3. Add test cases: `POST /coding-exercises/{exercise_id}/testcases/`
   - Each test case has `input_data`, `expected_output`, `position`, `is_hidden`, and optional `explanation`.
   - Hidden test cases are for grading only and are never shown to learners.
4. Delete a test case: positions of subsequent cases shift down by 1 automatically (no gaps).
5. Update exercise metadata (title, difficulty, problem statement) via `PATCH /coding-exercises/{exercise_id}/`.

### 6. Quiz authoring flow

1. Add quiz to section via `POST /sections/{section_id}/contents/` with `item_type: "quiz"`.
2. Add questions: `POST /quizzes/{quiz_id}/questions/` — each gets an ordered `position`.
3. Add answer options: `POST /quiz-questions/{question_id}/answers/`.
   - Exactly one `is_correct: true` answer is enforced per question at both serializer and DB levels.
4. Update or delete questions and answers via their respective detail endpoints.

---

## Main Endpoint Groups

### Auth (`/api/v1/auth/`)

- `POST register/`
- `POST login/`
- `POST token/refresh/`
- `POST logout/`
- `POST otp/verify/`
- `POST otp/resend/`
- `POST password/forgot/`
- `POST password/reset/`
- `POST password/change/`
- `GET/PATCH profile/me/`
- `GET/POST profile/me/education/`
- `GET/PATCH/DELETE profile/me/education/{id}/`
- `GET/POST profile/me/work-experience/`
- `GET/PATCH/DELETE profile/me/work-experience/{id}/`
- `GET profiles/learners/`
- `GET profiles/instructors/`
- `GET profiles/institutions/`
- `GET profiles/{slug}/`

#### OAuth

- `GET google/` → redirect to Google consent
- `GET google/callback/` → code received from Google
- `POST google/exchange-token/` → exchange code for local session
- `GET linkedin/` → redirect to LinkedIn consent
- `GET linkedin/callback/` → code received from LinkedIn
- `POST linkedin/exchange-token/` → exchange code for local session

---

### Verification (`/api/v1/verification/`)

#### Instructor

- `POST create/`
- `PATCH {id}/update/`
- `POST {id}/submit/`
- `GET my/`
- `GET my/{id}/`

#### Admin

- `GET admin/list/`
- `GET admin/{id}/`
- `POST admin/{id}/review/`

---

### Courses (`/api/v1/courses/`)

#### Courses

- `GET /` — list instructor's courses (paginated)
- `POST create/`
- `GET/PATCH/DELETE {course_id}/`

#### Course Metadata

- `GET/POST {course_id}/learning-objectives/`
- `GET/PATCH/DELETE learning-objectives/{item_id}/`
- `GET/POST {course_id}/prerequisites/`
- `GET/PATCH/DELETE prerequisites/{item_id}/`
- `GET/POST {course_id}/audiences/`
- `GET/PATCH/DELETE audiences/{item_id}/`

#### Sections

- `GET {course_id}/sections/`
- `POST {course_id}/sections/create/`
- `GET/PATCH/PUT/DELETE sections/{section_id}/`

#### Curriculum (mixed content)

- `GET/POST sections/{section_id}/contents/`
- `PATCH contents/{content_id}/reorder/`

#### Lecture Endpoints

- `GET sections/{section_id}/lectures/`
- `GET/PATCH/PUT/DELETE lectures/{lecture_id}/`

#### Quiz Endpoints

- `POST quizzes/`
- `GET/PATCH/DELETE quizzes/{quiz_id}/`
- `GET/POST quizzes/{quiz_id}/questions/`
- `GET/PATCH/DELETE quiz-questions/{question_id}/`
- `GET/POST quiz-questions/{question_id}/answers/`
- `GET/PATCH/DELETE quiz-answers/{answer_id}/`

#### Coding Exercise Endpoints

- `GET/PATCH/DELETE coding-exercises/{exercise_id}/`
- `GET/POST coding-exercises/{exercise_id}/language-configs/`
- `GET/PATCH/DELETE coding-exercises/{exercise_id}/language-configs/{config_id}/`
- `GET/POST coding-exercises/{exercise_id}/testcases/`
- `GET/PATCH/DELETE coding-exercises/{exercise_id}/testcases/{tc_id}/`

---

## Documentation

- Postman/API guide: [POSTMAN_TESTING_GUIDE.md](POSTMAN_TESTING_GUIDE.md)
- Courses testing guide: [COURSES_API_TESTING_GUIDE.md](COURSES_API_TESTING_GUIDE.md)
- Frontend error format: [FRONTEND_ERROR_RESPONSE_FORMAT.md](FRONTEND_ERROR_RESPONSE_FORMAT.md)
- Architecture onboarding docs: [docs/architecture/README.md](docs/architecture/README.md)

---

## Useful Commands

```bash
# migrations
python manage.py makemigrations
python manage.py migrate

# create admin
python manage.py createsuperuser

# run tests
python manage.py test
python manage.py test courses

# django checks
python manage.py check

# celery worker
celery -A career_college_backend worker -l info

# section content utilities
python manage.py populate_section_content --dry-run
python manage.py reindex_section_content_positions --dry-run
```

---

## Project Structure

```text
career_college_backend/
|-- career_college_backend/         # Django project config (settings, root urls)
|-- auth/                           # Auth, OTP, OAuth, profile APIs
|-- courses/                        # Courses, curriculum, lectures, quizzes, coding exercises
|   |-- all_views/
|   |   |-- course_views.py         # Course list/create/detail
|   |   |-- content_views.py        # Sections, SectionContent, lectures, quizzes
|   |   `-- coding_views.py         # Coding exercises, language configs, test cases
|   |-- models.py                   # All course domain models
|   |-- serializers.py              # All serializers
|   |-- services.py                 # Curriculum ordering, video pipeline helpers
|   |-- selectors.py                # Reusable query helpers
|   |-- tasks.py                    # Celery tasks (video transcoding)
|   `-- transcoding.py              # FFmpeg transcoding routines
|-- id_verification/                # Instructor identity verification workflow
|-- core/                           # Shared permissions, pagination, middleware
|-- docs/architecture/              # New-developer architecture and workflow docs
|-- templates/                      # Email templates
|-- manage.py
|-- requirements.txt
|-- .env.example
|-- README.md
|-- POSTMAN_TESTING_GUIDE.md
|-- COURSES_API_TESTING_GUIDE.md
`-- FRONTEND_ERROR_RESPONSE_FORMAT.md
```

---

## Notes

- `SectionContent.position` is the single source of truth for curriculum ordering within a section.
- Lecture, Quiz, and CodingExercise are domain objects; placement and ordering are tracked separately via `SectionContent`.
- `solution_code` on `CodingExerciseLanguageConfig` is instructor-only and must never be included in learner-facing serializers.
- Hidden test cases (`CodingTestCase.is_hidden = True`) are used for grading only and are never returned to learners.
- Logging safely falls back to console when file logging is not writable.
- Keep `.env` private; only commit `.env.example`.
