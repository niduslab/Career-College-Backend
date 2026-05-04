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
- `courses`: course authoring, curriculum, lectures, quizzes
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

- Custom email-based user model
- OTP verification (registration/password reset)
- JWT auth with refresh flow
- Google + LinkedIn OAuth entry points
- Role-aware permissions (learner/instructor/partner/admin)

### Profiles

- Learner, Instructor, Partner Institution profile models
- Education and work experience support
- Public profile browsing endpoints

### Instructor Verification

- Verification state machine: `draft -> submitted -> under_review -> approved/rejected/action_required`
- Admin review endpoints
- Approval auto-marks instructor profile as verified

### Course Authoring

- Course category + course + sections
- Mixed curriculum ordering via `SectionContent`
- Learning objectives, prerequisites, audience entries
- Reorder API with shifting logic

### Lecture/Video

- Article and video lecture types
- Video assets + processing jobs
- Async transcoding pipeline with Celery

### Quizzes

- Practice quiz model (no passing score/time limit)
- Quiz questions and answers
- One correct answer max per question
- Quiz placement in curriculum through `SectionContent`

## Main Endpoint Groups

## Auth (`/api/v1/auth/`)

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

### OAuth endpoints

- `GET google/`
- `GET google/callback/`
- `POST google/exchange-token/`
- `GET linkedin/`
- `GET linkedin/callback/`
- `POST linkedin/exchange-token/`

## Verification (`/api/v1/verification/`)

### Instructor

- `POST create/`
- `PATCH {id}/update/`
- `POST {id}/submit/`
- `GET my/`
- `GET my/{id}/`

### Admin

- `GET admin/list/`
- `GET admin/{id}/`
- `POST admin/{id}/review/`

## Courses (`/api/v1/courses/`)

### Courses

- `GET /`
- `POST create/`
- `GET/PATCH/DELETE {course_id}/`

### Metadata

- Learning objectives CRUD
- Prerequisites CRUD
- Audience CRUD

### Sections

- `GET {course_id}/sections/`
- `POST {course_id}/sections/create/`
- `GET/PATCH/PUT/DELETE sections/{section_id}/`

### Curriculum (mixed content)

- `GET/POST sections/{section_id}/contents/`
- `PATCH contents/{content_id}/reorder/`

### Legacy Lecture Endpoints

- `GET sections/{section_id}/lectures/`
- `POST sections/{section_id}/lectures/create/`
- `GET/PATCH/PUT/DELETE lectures/{lecture_id}/`

### Quiz Endpoints

- `POST quizzes/`
- `GET/PATCH/DELETE quizzes/{quiz_id}/`
- `GET/POST quizzes/{quiz_id}/questions/`
- `GET/PATCH/DELETE quiz-questions/{question_id}/`
- `GET/POST quiz-questions/{question_id}/answers/`
- `GET/PATCH/DELETE quiz-answers/{answer_id}/`

## Documentation

- Postman/API guide: [POSTMAN_TESTING_GUIDE.md](POSTMAN_TESTING_GUIDE.md)
- Courses testing guide: [COURSES_API_TESTING_GUIDE.md](COURSES_API_TESTING_GUIDE.md)
- Frontend error format: [FRONTEND_ERROR_RESPONSE_FORMAT.md](FRONTEND_ERROR_RESPONSE_FORMAT.md)
- Architecture onboarding docs: [docs/architecture/README.md](docs/architecture/README.md)

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

## Project Structure

```text
career_college_backend/
|-- career_college_backend/         # Django project config
|-- auth/                           # Auth, OTP, OAuth, profile APIs
|-- courses/                        # Courses, curriculum, lectures, quizzes, video pipeline
|-- id_verification/                # Instructor identity verification workflow
|-- core/                           # Shared permissions/pagination/middleware
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

## Notes

- `SectionContent.position` is the source of truth for curriculum ordering.
- Quiz and lecture are content objects; placement is tracked separately via `SectionContent`.
- Logging safely falls back to console when file logging is not writable.
- Keep `.env` private; only commit `.env.example`.
