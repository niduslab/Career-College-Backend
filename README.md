# Career College Backend

A Django REST Framework backend for a course marketplace platform. The platform has four user roles — **learner**, **instructor**, **partner institution**, and **admin**.

**Instructors and verified partner institutions** create and publish courses with mixed content (lectures, quizzes, coding exercises, assignments), upload videos that are async-transcoded to HLS, and must pass identity / institution verification before they can author content. Partner institutions additionally onboard their own teaching staff ("experts"), organise them into departments, staff their courses' instructor rosters directly, and run live webinars (an assigned expert publishes; the platform handles catalog + registration while delivery links out to Zoom/Meet/Jitsi).

**Learners** browse and enroll in published courses, consume curriculum (video/article lectures, quizzes, assignments, coding exercises), earn completion certificates, leave reviews & ratings, register for live webinars, and message instructors. Notifications (in-app + email) and messaging are delivered in real time over a multiplexed WebSocket.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Framework | Django 5.x + Django REST Framework 3.x |
| Auth | Simple JWT (access + refresh tokens) + django-allauth (OAuth) |
| Database | PostgreSQL (psycopg2-binary) |
| Task queue | Celery 5.x + Redis (beat for scheduled tasks) |
| Realtime | Django Channels (ASGI) + Redis channel layer — multiplexed WebSocket for notifications & messaging |
| Video processing | FFmpeg / FFprobe (via ffmpeg-python) |
| Code execution sandbox | Docker (one container per submission; docker SDK 7.x) |
| Media storage | Local filesystem (configurable via `MEDIA_ROOT`) |
| Production server | Gunicorn |

---

## Apps

| App | URL prefix | Responsibility |
|-----|-----------|----------------|
| `authentication` | `/api/v1/auth/` | Registration, OTP, JWT, OAuth (Google/LinkedIn), profiles, partner-institution experts & departments |
| `courses` | `/api/v1/courses/` | Public catalog, learner enrollment, my-courses dashboard, course authoring/curriculum (instructor + partner institution), certificates, reviews & ratings |
| `id_verification` | `/api/v1/verification/` | Instructor identity **and** partner-institution credential verification state machines |
| `messaging` | `/api/v1/messaging/` | Learner ↔ instructor direct messaging (REST + WebSocket) |
| `notifications` | `/api/v1/notifications/` | In-app notification feed, email preferences, dispatcher |
| `realtime` | `/ws/` | ASGI WebSocket consumer multiplexing the `notifications` and `messaging` streams |
| `webinars` | `/api/v1/webinars/` | Institution-owned live webinars (external meeting link), publish state machine, public catalog + learner registration |
| `analytics` | `/api/v1/analytics/` | Read-only partner-institution analytics dashboard (KPI summary, trends, top-courses); aggregates across apps, owns no models |
| `core` | — | Shared permissions, pagination, middleware |

---

## Development Setup

### Prerequisites

- Python 3.11+
- PostgreSQL running locally
- Redis running locally (required for Celery)
- FFmpeg and FFprobe installed (required for video transcoding)
- Docker Desktop / engine running (required for coding-exercise execution). Windows learners also need `pywin32` so the docker SDK can talk to the named-pipe daemon — installed automatically via `pip install -r requirements.txt`.

### 1. Clone and create a virtual environment

```powershell
git clone <repo-url>
cd Career-College-Backend

python -m venv .venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell
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

### 7. Start the Celery worker (required for transactional email, video transcoding, assignment grading, and coding-exercise execution)

> All auth emails (OTP for registration/resend/password-reset, and institution-onboarded expert credentials) are sent **asynchronously** by the worker. With no worker running, those emails are never delivered.

Open a second terminal, activate the venv, then:

```bash
# Windows (solo pool avoids multiprocessing issues)
celery -A career_college_backend worker --loglevel=info --pool=solo -Q celery,notifications

# macOS / Linux
celery -A career_college_backend worker --loglevel=info -Q celery,notifications
```

> `-Q celery,notifications` is required: auth/transactional emails and the coding/video tasks run on the default `celery` queue, while notification emails are routed to a separate `notifications` queue (`CELERY_TASK_ROUTES` in `settings.py`). A bare worker consumes only `celery` and would silently never deliver notification emails.

### 8. Start Celery beat (required for the coding-submission zombie reaper)

A scheduled task flips `CodingSubmission` rows stuck in `queued`/`grading` for more than 5 minutes to `error`, so polling UIs don't hang on worker crashes. Open a third terminal:

```bash
celery -A career_college_backend beat --loglevel=info
```

Skip this in pure-dev sessions if you don't intend to test the reaper.

### 9. Pre-pull the coding runner images (recommended)

The runner pulls these on first use, but unauthenticated Docker Hub pulls are rate-limited. Pull once up front:

```bash
docker pull python:3.12-slim
docker pull node:20-alpine
docker pull gcc:14
docker pull eclipse-temurin:21-jdk-alpine
```

Override any of them with `RUNNER_IMAGE_PYTHON` / `RUNNER_IMAGE_JAVASCRIPT` / `RUNNER_IMAGE_CPP` / `RUNNER_IMAGE_JAVA` env vars (see below).

### 10. Install gVisor (`runsc`) on production / shared hosts

The coding runner picks its Docker runtime from `DEBUG`: local dev (`DEBUG=True`) defaults to Docker's stock `runc` so gVisor doesn't need to be installed; production (`DEBUG=False`) defaults to `runsc` (gVisor), which provides user-space syscall interception and is the recommended runtime for any host running untrusted learner code. Override either default by setting `RUNNER_RUNTIME=runc` or `RUNNER_RUNTIME=runsc` in `.env`.

For **production / shared hosts** (or any local box you want to test the gVisor path on):

1. Install gVisor (`runsc`):

   ```bash
   sudo apt-get install -y apt-transport-https ca-certificates curl gnupg
   curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
   echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | sudo tee /etc/apt/sources.list.d/gvisor.list
   sudo apt-get update && sudo apt-get install -y runsc
   ```

2. Register the runtime with Docker — add to `/etc/docker/daemon.json`:

   ```json
   {
     "runtimes": {
       "runsc": { "path": "/usr/bin/runsc" }
     }
   }
   ```

   Then reload the daemon:

   ```bash
   sudo systemctl restart docker
   ```

3. Verify:

   ```bash
   docker run --rm --runtime=runsc alpine dmesg | head
   ```

   The output should show the gVisor banner (`Starting gVisor...`). If the command fails, gVisor is not registered correctly.

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
| `CELERY_RESULT_EXPIRES` | No | `3600` | TTL (seconds) for Celery results. Coding-exercise Run task results expire after this — frontend polling should give up past this window. |
| `RUNNER_RUNTIME` | No | `runc` if `DEBUG=True`, else `runsc` | Docker runtime used to launch coding-exercise containers. Defaults to `runc` in dev (no gVisor needed) and `runsc` (gVisor) in prod for syscall-level isolation. Override explicitly to flip either default. |
| `RUNNER_IMAGE_PYTHON` | No | `python:3.12-slim` | Override the Python runner container image. |
| `RUNNER_IMAGE_JAVASCRIPT` | No | `node:20-alpine` | Override the JS runner container image. |
| `RUNNER_IMAGE_CPP` | No | `gcc:14` | Override the C++ runner container image. |
| `RUNNER_IMAGE_JAVA` | No | `eclipse-temurin:21-jdk-alpine` | Override the Java runner container image. |
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
2. Set metadata: `learning_objectives`, `prerequisites`, `audiences` — newline-separated text fields in the create/PATCH payload.
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

### 6. Coding-exercise execution (Run vs Submit)

Two execution paths feed code into the Docker runner — they look similar but have different persistence and visibility semantics:

| Mode | Endpoint | Persisted? | Test cases run | Returns | Poll via |
|---|---|---|---|---|---|
| **Run** | `POST /learn/coding-exercises/{id}/run/` | No — Celery result only (expires per `CELERY_RESULT_EXPIRES`) | Visible only (`is_hidden=False`) | `{task_id}` (HTTP 202) | `GET /learn/coding-exercises/tasks/{task_id}/` |
| **Submit** | `POST /learn/coding-exercises/{id}/submit/` | Yes — `CodingSubmission` row | All (visible + hidden) | Queued submission (HTTP 202) | `GET /learn/coding-exercises/submissions/{id}/` |

Pipeline:

1. View dispatches a Celery task on commit (`evaluate_coding_run_task` for Run, `evaluate_coding_submission_task` for Submit). The Submit row is created in `status='queued'` first so the frontend always has an ID to poll.
2. The Celery worker invokes `CodeRunner.run_submission(code, test_cases, time_limit_ms, language)` ([courses/services/code_runner.py](courses/services/code_runner.py)) which spins up **one Docker container per submission** (not per test case) and runs every test in a batched per-language harness. This eliminates `(N-1)` container startups + `(N-1)` compiles for C++/Java vs the naive model.
3. Sandbox per container: `runtime='runsc'` (gVisor; configurable via `RUNNER_RUNTIME`), `network_disabled=True`, `mem_limit=128m`, `memswap_limit=128m`, `nano_cpus=500_000_000` (0.5 cores), `pids_limit=64` (fork-bomb cap), `ulimits=[fsize=10MB, nproc=64, nofile=128, cpu=10s]`, `read_only=True`, `tmpfs={'/tmp': 'size=32m,exec'}`, `cap_drop=['ALL']`, `security_opt=['no-new-privileges:true']`. Wall-clock budget is `container.wait(timeout=...)` + `container.kill()` on overshoot.
4. Learner code must define a top-level **`solve(input_string)`** function (Python/JavaScript) or `void solve(const std::string&)` (C++) / `static void solve(String)` (Java). The harness loops over `INPUT_0..INPUT_{N-1}` env vars, calls `solve()` per test inside a try/except so one failing test doesn't kill the batch, and emits sentinel-delimited per-test results on stdout.
5. Submit task writes one `CodingSubmissionTestResult` per test, computes aggregates (`passed_tests`, `score = round(passed/total*100, 2)`, status precedence `error > failed > passed`), and on PASS schedules `recalculate_progress` via `transaction.on_commit`.
6. Hidden tests are **omitted entirely** from learner-facing `test_results`. Aggregate counts still include them so the learner sees overall pass/fail.
7. Retries: `evaluate_coding_submission_task` is `acks_late=True`, idempotent (early-returns on terminal status), and `autoretry_for=(DockerTransientError,)` with `max_retries=3`. Learner-code errors aren't retried — they're terminal already.
8. Zombie reaper (`reap_stuck_coding_submissions_task`, scheduled every 60 s via Celery beat) flips `queued`/`grading` rows older than 5 min to `error`. Required when running in dev — see Section 8.

**WARNING (echoed from CLAUDE.md):** the runner is Docker-out-of-Docker. The Docker daemon socket is shared with the host; a sufficiently advanced attacker who can run code inside a container can still escape to the host daemon. gVisor (`runsc`) mitigates the syscall-level attack surface inside the container but does not eliminate the daemon-socket risk on the host side. Demo / single-tenant use only — for multi-tenant prod, move the runner to a dedicated host or use Kata Containers / Firecracker for hardware-level isolation.

Tests must **never** hit real Docker. Patch `courses.services.code_runner.CodeRunner.run_submission` to return deterministic `SingleTestResult` lists; Celery runs in eager mode in tests so `.delay()` executes synchronously. End-to-end smoke verification against real Docker lives in `scripts/smoke_code_runner.py` and `scripts/smoke_runtime_error.py` — manual, not part of `manage.py test`.

### 7. Learner enrollment and access

1. Public users browse `GET /api/v1/courses/catalog/` and `GET /api/v1/courses/catalog/{slug}/`.
2. Authenticated learners enroll via `POST /api/v1/courses/{slug}/enroll/`.
3. Enrollments are unique per learner+course; re-enrolling reactivates the existing record.
4. The course-player page is composed from three endpoints: `GET /api/v1/courses/my-courses/{slug}/` returns the slim metadata header (course info + overall progress); `GET /api/v1/courses/learn/{slug}/curriculum/` returns the sidebar curriculum outline; `GET /api/v1/courses/learn/lectures/{id}/` returns a single playable lecture. Watch progress is upserted via `POST /api/v1/courses/learn/lectures/{id}/progress/`.
5. The dashboard "My Courses" list is at `GET /api/v1/courses/my-courses/`.
6. Learners can soft-unenroll via `POST /api/v1/courses/{slug}/unenroll/`; progress stays preserved.

### 8. Messaging (learner↔instructor · co-instructor · institution↔expert)

A `Conversation` is a role-neutral 2-party thread selected by `conversation_type`. The send-gate is dispatched by type; parties live in `ConversationParticipant` (per-user read cursor).

1. Open a thread with `POST /api/v1/messaging/conversations/create/` — `conversation_type` (default `learner_instructor`) plus the required ids: `learner_instructor` → `{course_id, instructor_id}` (learner-initiated); `co_instructor` → `{course_id, peer_instructor_id}` (instructor-initiated); `institution_expert` → `{expert_user_id, course_id?}` (institution-initiated). Idempotent — returns the existing thread (200) if the pair already has one. Only the **opener** message is persisted here.
2. **Follow-up messages are sent over the `messaging` WebSocket stream only** (`send_message`) — there is no REST send endpoint. The send-gate runs there (per type: active enrollment / instructor-on-course / active affiliation *at send time*) and returns an `error` frame on violation.
3. Real-time delivery: the recipient receives a `new_message` frame on their `messaging_user_{id}` group (the sender does **not** — they got the `message_sent` ack). A `message.received` notification + optional email also fire (course context omitted for course-less institution↔expert threads).
4. Unread tracking is cursor-based: `POST /api/v1/messaging/conversations/{id}/read/` (or the `mark_read` WS action) stamps the caller's participant cursor. `GET /api/v1/messaging/conversations/unread-count/` returns the number of conversations with ≥1 unread message (inbox badge).
5. Either party can always read historical messages, even after unenrollment / instructor removal / expert deactivation.

See [docs/future_implementations/INSTITUTION_MESSAGING.md](docs/future_implementations/INSTITUTION_MESSAGING.md) for the current model and [docs/architecture/17-messaging-system.md](docs/architecture/17-messaging-system.md) for the WS protocol (note: its body predates the generalization — see the banner).

### 9. Partner institution onboarding & course staffing

1. Register a `partner_institution` account; verify email.
2. `POST /api/v1/verification/institution/create/` → draft; `PATCH .../{id}/update/` fills `registration_number`, `issuing_authority`, `accreditation_document`; `POST .../{id}/submit/` → `submitted` (notifies admins).
3. Admin approves via `POST /api/v1/verification/admin/institution/{id}/review/` `{"action": "approve"}` → `PartnerInstitutionProfile.is_verified = True`. The `IsVerifiedPartnerInstitution` gate now passes.
4. Define departments: `POST /api/v1/auth/partner/departments/`.
5. Onboard experts: `POST /api/v1/auth/partner/experts/` — auto-provisions an `instructor` account (`is_verified=True`, `is_email_verified=True`) and emails login credentials. No OTP/identity step — the institution vouches.
6. Create a course through the same `POST /api/v1/courses/create/` instructors use (`partner_institution` set automatically; instructor roster left empty). Add curriculum as in Workflow 2–3.
7. Staff the roster: `POST /api/v1/courses/{pk}/institution-instructors/` (body `expert_user_id`) adds an active expert directly — no invite/accept. Assigned experts edit content via the normal authoring endpoints.
8. Submit for review (`POST /api/v1/courses/{id}/submit/`) — same lifecycle as instructor-authored courses.

See [docs/architecture/18-partner-institutions.md](docs/architecture/18-partner-institutions.md) for the verification state machine, expert provisioning, departments, and roster rules.

### 10. Webinars (institution-owned live sessions)

1. A verified partner institution creates a webinar via `POST /api/v1/webinars/create/` — metadata + an external meeting link (`meeting_url`), scheduled time, capacity, and (optionally) `guest_speakers` (external, no account) and `institutional_speaker_ids` (platform experts credited, credit-only). Status starts `draft`.
2. Assign a host: `POST /api/v1/webinars/{pk}/host/` (body `expert_user_id`) — an active affiliated expert who will publish and lead.
3. The **host expert** publishes directly: `POST /api/v1/webinars/{pk}/publish/` → `published`. No admin or institution review gate. A completeness check runs (title, description, future `scheduled_at`, `duration_minutes`, `meeting_url`, host assigned). The institution owner is *not* the host, so it cannot publish (→ 404).
4. Published webinars appear in the public catalog: `GET /api/v1/webinars/catalog/` and `catalog/{slug}/` (no `meeting_url`).
5. Learners register: `POST /api/v1/webinars/{slug}/register/`. Capacity is enforced; a `WEBINAR_REGISTERED` notification fires. The join link is exposed only to registrants at `GET /api/v1/webinars/my-webinars/{slug}/`.
6. Lifecycle: `POST {pk}/archive/` (`published → archived`, owner/host/admin) and `POST {pk}/rework/` (`archived → draft`, owner/host). Editing is institution-only (the host can read but not PATCH).

See [docs/architecture/19-webinars.md](docs/architecture/19-webinars.md) for presenter roles, the publish state machine, serializers, and notification wiring.

### 11. Partner institution analytics dashboard

1. A verified institution loads `GET /api/v1/analytics/partner/summary/` — one payload of KPI cards: course counts + status breakdown + weighted avg rating, enrollment totals/growth/active-learners/completion-rate, certificates issued, webinar status + upcoming/live/completed + registrations, roster size, and a composite engagement score.
2. Charts fetch trends lazily: `analytics/partner/enrollments/trend/`, `webinars/trend/`, `certificates/trend/` — each `?granularity=monthly|weekly&periods=N` (zero-filled contiguous series).
3. `analytics/partner/top-courses/?sort=enrollments|rating|completion&limit=N` ranks the institution's courses.
4. `analytics/partner/experts/performance/` drills to per-expert outcomes (ratings, enrollments, completion, certificates, content authored, webinars hosted) for the whole roster; `.../experts/{id}/performance/` for one. A course is credited to every instructor + its creator.
5. Everything is read-only and scoped to the caller's own institution (no resource id → the only failure mode is 403, except the numeric expert-id detail which is 404 for a non-affiliate). `revenue` is disabled (no payments model) and webinar attendance is flagged off until the live-day join flow ships.

See [docs/architecture/20-analytics-dashboard.md](docs/architecture/20-analytics-dashboard.md) for metrics, query strategy, and the revenue/attendance caveats.

### 12. Paid course / webinar purchase (SSLCommerz sandbox)

1. Learner hits `POST /api/v1/payments/checkout/` with exactly one of `{"course_slug": "..."}` or `{"webinar_slug": "..."}` on a published target with `price > 0` → the backend opens an SSLCommerz hosted-checkout session and returns a `gateway_url` (+ `tran_id`, `item_type`).
2. The frontend redirects the browser to `gateway_url`; the learner pays on SSLCommerz's page (sandbox card `4111 1111 1111 1111`).
3. The gateway redirects back to the backend `success/` callback, which **re-validates** the payment via the SSLCommerz Validation API (`val_id`) — amount, currency, tran_id, and store_id are all checked against the order snapshot. Only then is the order marked `paid` and the access granted atomically: a **PAID enrollment** (course) or an **active registration** (webinar). The browser is then 302'd to the frontend success page. The server-to-server IPN funnels into the same idempotent finalize as a safety net.
4. Fail/cancel at the gateway mark the order `failed`/`cancelled` and redirect accordingly; a completed (`paid`) order can never be clobbered by a late callback.
5. The free-enroll endpoint (`POST /courses/{slug}/enroll/`) and the webinar register endpoint (`POST /webinars/{slug}/register/`) reject paid targets with 422 unless the learner already has a `paid` order — so unenroll → re-enroll (or cancel → re-register) never double-charges.

See [docs/architecture/21-payments.md](docs/architecture/21-payments.md) for the trust model and edge-case policies; [docs/api-testing/postman-payments.md](docs/api-testing/postman-payments.md) is the sandbox walkthrough.

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

#### Partner Institution (institution admin)

All require a **verified** partner institution (`IsVerifiedPartnerInstitution`); scoped to the caller's own institution; numeric IDs → 404 on no-access. See [docs/api-testing/postman-partner-institution.md](docs/api-testing/postman-partner-institution.md).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `partner/experts/` | List / onboard experts (auto-provision instructor + emailed credentials) |
| GET/PATCH | `partner/experts/{id}/` | Expert detail / edit (profile, `department_id`, activate-deactivate) |
| GET/POST | `partner/departments/` | List / create institution departments |
| GET/PATCH/DELETE | `partner/departments/{id}/` | Department detail / rename or toggle active / soft-deactivate |

> Institution analytics moved to its own app — see the **Analytics** section below.

---

### Identity & Institution Verification — `/api/v1/verification/`

#### Instructor (identity)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `create/` | Create a verification draft |
| PATCH | `{id}/update/` | Upload identity documents |
| POST | `{id}/submit/` | Submit for admin review |
| GET | `my/` | List own verification submissions |
| GET | `my/{id}/` | Detail view of own submission |

#### Admin (identity)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `admin/list/` | List all submissions |
| GET | `admin/{id}/` | Detail view |
| POST | `admin/{id}/review/` | Approve, reject, or request action |

#### Partner Institution (credential verification)

Institution-facing — gated `IsEmailVerified` + a `user_type == 'partner_institution'` guard (**not** `IsVerifiedPartnerInstitution`; verification is the gate being cleared). Numeric IDs → 404 on no-access. No `expired` state. See [docs/architecture/18-partner-institutions.md](docs/architecture/18-partner-institutions.md).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `institution/create/` | Create a draft institution verification |
| PATCH | `institution/{id}/update/` | Fill credential fields (`registration_number`, `issuing_authority`, `accreditation_document`, …) |
| POST | `institution/{id}/submit/` | Submit for admin review |
| GET | `institution/my/` | List own institution verifications |
| GET | `institution/my/{id}/` | Detail view of own submission |

#### Admin (institution)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `admin/institution/list/` | List all institution submissions (filter `?status=`) |
| GET | `admin/institution/{id}/` | Detail view (incl. `admin_notes`) |
| POST | `admin/institution/{id}/review/` | Approve (→ institution `is_verified=True`), reject, or request action. `expire` → 422 |

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
| GET | `my-courses/{slug}/` | Learner or course instructor | Slim course-header metadata + caller's enrollment status (no curriculum tree — see `learn/{slug}/curriculum/`) |
| GET | `my-courses/{slug}/certificate/` | Learner | Retrieve own completion certificate for a course. 404 if not yet completed. |

#### Certificates

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `certificates/{uuid}/verify/` | No | Public share/verification page — returns certificate metadata + `is_valid: true` |
| GET | `certificates/{uuid}/download/` | No | Download certificate as a PDF (generated on-the-fly via reportlab; no file stored on disk) |

#### Course Reviews & Ratings

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `{slug}/reviews/summary/` | No | Aggregate stats: `avg_rating`, `review_count`, 1–5 star distribution |
| GET | `{slug}/reviews/` | No | Paginated published reviews. `?rating=1-5`, `?ordering=(-created_at\|created_at\|-helpful_count\|-rating\|rating)` |
| POST | `{slug}/reviews/` | Learner | Create or replace own review (upsert). Caller must be actively enrolled. 201 on create, 200 on update. |
| GET | `{slug}/reviews/my-review/` | Learner | Fetch own review for this course. 404 if none exists yet. |
| PATCH | `{slug}/reviews/my-review/` | Learner | Update own review. |
| DELETE | `{slug}/reviews/my-review/` | Learner | Delete own review. Recalculates course `avg_rating` on commit. |
| POST | `reviews/{review_id}/vote/` | Learner | Cast or flip a helpful / not-helpful vote. 422 on self-vote. |

#### Learner Consumption (Phase 1 + Phase 2)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `learn/{slug}/curriculum/` | Enrolled learner or course instructor | Lightweight ordered curriculum: sections + items with title, type, duration, and per-lecture completion marker |
| GET | `learn/lectures/{lecture_id}/` | Enrolled learner or course instructor | Single lecture: HLS playlist + renditions for video, article text for article. Includes the caller's progress to support resume |
| POST | `learn/lectures/{lecture_id}/progress/` | Enrolled learner | Idempotent upsert of `WatchProgress` (`watched_seconds`, `is_completed`). The `WatchProgress` post_save signal recalculates the enrollment's `progress_percent` |
| GET | `learn/quizzes/{quiz_id}/` | Enrolled learner or course instructor | Quiz + questions + answer options (no `is_correct`) for the attempt UI. Includes the caller's `latest_attempt` summary |
| POST | `learn/quizzes/{quiz_id}/submit/` | Enrolled learner | Submit selected answers; returns score + per-question verdict (with the correct answer revealed only for wrong ones). Creates a new `QuizAttempt` each call |
| GET | `learn/assignments/{assignment_id}/` | Enrolled learner or course instructor | Assignment + questions for the attempt UI (`model_answer`/`rubric` always stripped). Includes the caller's `latest_submission` summary |
| POST | `learn/assignments/{assignment_id}/submit/` | Enrolled learner | Create `AssignmentSubmission(status='submitted')` + per-question answers (rubric snapshot frozen). Dispatches `grade_assignment_submission_task`. Returns `202` |
| GET | `learn/assignments/submissions/{submission_id}/` | Owner learner | Poll a submission; `model_answer` revealed only when `status in (passed, failed)` |
| POST | `learn/assignments/submissions/{submission_id}/retry/` | Owner learner | Re-enqueue grading for a submission stuck in `grading_failed` (reuses the row) |
| GET | `learn/coding-exercises/{exercise_id}/` | Enrolled learner or course instructor | Coding exercise detail with starter code + visible test cases. `solution_code` and hidden test cases are never present |
| POST | `learn/coding-exercises/{exercise_id}/run/` | Enrolled learner | Transient run against visible test cases only. Returns `{task_id}` for polling. No DB row, no progress update |
| GET | `learn/coding-exercises/tasks/{task_id}/` | Verified-email JWT | Poll a Run task. States: `PENDING` / `STARTED` / `SUCCESS` (with `result` dict) / `FAILURE` |
| POST | `learn/coding-exercises/{exercise_id}/submit/` | Enrolled learner | Persisted submission. Creates `CodingSubmission(status='queued')`, dispatches `evaluate_coding_submission_task`. Returns `202` |
| GET | `learn/coding-exercises/submissions/{submission_id}/` | Owner learner | Poll a submission. Hidden test rows are omitted from `test_results` entirely; aggregate counts still include them |
| POST | `learn/coding-exercises/submissions/{submission_id}/retry/` | Owner learner | Re-enqueue evaluation for a submission stuck in `error`. Reuses the row. Only `error` is retryable |

`recalculate_progress` now counts lectures (`WatchProgress.is_completed=True`), quizzes (≥1 `QuizAttempt`), assignments (`AssignmentSubmission.status='passed'`), and coding exercises (`CodingSubmission.status='passed'`, distinct per exercise). Aggregate `progress_percent` updates via signals + `transaction.on_commit` hooks.

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

`learning_objectives`, `prerequisites`, and `audiences` are newline-separated `TextField`s on the
course (one item per line). Set/read them via the course create (`POST /courses/create/`), update
(`PATCH /courses/{id}/`), and detail responses — there are no dedicated metadata endpoints.

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

#### Coding Exercises (Instructor Authoring)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/PATCH/DELETE | `coding-exercises/{exercise_id}/` | Exercise detail |
| GET/POST | `coding-exercises/{exercise_id}/language-configs/` | List or add language configs |
| GET/PATCH/DELETE | `coding-exercises/{exercise_id}/language-configs/{config_id}/` | Config detail |
| GET/POST | `coding-exercises/{exercise_id}/testcases/` | List or add test cases |
| GET/PATCH/DELETE | `coding-exercises/{exercise_id}/testcases/{tc_id}/` | Test case detail |

For the learner-side Run / Submit / poll / retry endpoints, see *Learner Consumption* above and the workflow walk-through in *Key Workflows → 6. Coding-exercise execution*.

#### Partner Institution Course Roster

Gated `IsVerifiedPartnerInstitution`; only the owning institution; numeric pk → 404. Direct add/remove of an **active affiliated expert** (no invite/accept), only while the course `is_editable()`. See [docs/architecture/18-partner-institutions.md](docs/architecture/18-partner-institutions.md).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `{pk}/institution-instructors/` | Add an active expert to the course roster (body: `expert_user_id`) |
| DELETE | `{pk}/institution-instructors/{expert_user_id}/` | Remove an expert from the course roster |

---

### Messaging — `/api/v1/messaging/`

All endpoints require `IsEmailVerified` + learner/instructor/partner-institution user type (admins excluded). Numeric IDs → 404 on no-access (project-wide rule).

A `Conversation` is a role-neutral 2-party thread selected by `conversation_type`: `learner_instructor` (learner-initiated, course required), `co_instructor` (instructor↔instructor on a course), `institution_expert` (institution-initiated, course optional). The send-gate is dispatched by type in the service. See [docs/future_implementations/INSTITUTION_MESSAGING.md](docs/future_implementations/INSTITUTION_MESSAGING.md).

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `conversations/` | Participant | Paginated inbox, newest-active first. Each row carries `conversation_type`, `participants`, and the caller's `unread_count` |
| GET | `conversations/unread-count/` | Participant | `{unread_conversations: N}` — count of threads with ≥1 unread message (inbox badge) |
| POST | `conversations/create/` | Type-dependent initiator | Initiate a thread. Body: `conversation_type` (default `learner_instructor`) + the required ids — `{course_id, instructor_id}` / `{course_id, peer_instructor_id}` / `{expert_user_id, course_id?}` + `body`. 201 on create, 200 if it already exists (idempotent) |
| GET | `conversations/{id}/` | Participant | Thread metadata + paginated messages (oldest-first). Does **not** mark read |
| POST | `conversations/{id}/read/` | Participant | Stamp the caller's participant read cursor (one UPDATE) |

> Follow-up messages have **no REST endpoint** — they are sent over the WebSocket `messaging` stream (`send_message`). The create call persists the opener; every reply flows through WS. Send-gate violations return a WS `error` frame, not an HTTP status.

#### WebSocket — `/ws/` (stream `messaging`)

Multiplexed over the shared `PlatformConsumer`. Connect with `ws://host/ws/?token=<raw JWT>`.

| Direction | Frame `type` | Notes |
|---|---|---|
| client → server | `send_message` | `{conversation_id, body}` — same send-gate as REST |
| client → server | `mark_read` | `{conversation_id}` |
| server → client | `message_sent` | Ack to the sender after insert (sender's only frame) |
| server → client | `new_message` | Pushed only to the **recipient**'s group |
| server → client | `marked_read` | Ack to the caller |
| server → client | `unread_summary` | Pushed on connect: per-conversation unread counts + `unread_conversations` total |
| server → client | `error` | `{detail}` — connection stays open |

---

### Notifications — `/api/v1/notifications/`

All endpoints require `IsAuthenticated` + `IsEmailVerified`. In-app notifications are also pushed in real time over the `notifications` WebSocket stream (`/ws/`). See [docs/architecture/16-notification-system.md](docs/architecture/16-notification-system.md).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `` (root) | Paginated notification feed for the caller (newest first) |
| GET | `unread-count/` | `{unread: N}` — unread-notification badge count |
| POST | `mark-read/` | Mark notifications read (specific ids, or all) |
| GET/PATCH | `preferences/` | View or update per-category email notification preferences |

---

### Webinars — `/api/v1/webinars/`

Institution-owned live webinars. Slug endpoints → 403 on no-access; numeric-ID endpoints → 404. `meeting_url` is registrant-only (never in the catalog). See [docs/architecture/19-webinars.md](docs/architecture/19-webinars.md).

#### Public Catalog

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `catalog/` | No | List published webinars, soonest first. `?category=<id>`, `?upcoming=true` |
| GET | `catalog/{slug}/` | No | Published webinar detail (no `meeting_url`) |

#### Learner Registration

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `{slug}/register/` | Learner | Register for a published webinar. 201; duplicate → 422; capacity reached → 422 |
| GET | `my-webinars/` | Learner | Own active registrations (paginated) |
| GET | `my-webinars/{slug}/` | Learner | Registrant detail — exposes `meeting_url`. 403 if not registered |

#### Authoring (Partner Institution)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `` (root) | Verified course creator | Own webinars (owner **or** assigned host), paginated |
| POST | `create/` | Verified partner institution | Create a webinar draft |
| GET | `{pk}/` | Verified course creator (owner or host) | Webinar detail |
| PATCH | `{pk}/` | Verified partner institution (owner) | Edit metadata (host cannot PATCH → 404) |
| POST/DELETE | `{pk}/host/` | Verified partner institution | Assign / clear the host expert (body: `expert_user_id`) |

#### Status Transitions

| Method | Endpoint | Who | Transition |
|--------|----------|-----|-----------|
| POST | `{pk}/publish/` | Assigned host expert | `draft → published` (completeness check; institution user → 404) |
| POST | `{pk}/archive/` | Owner / host / admin | `published → archived` |
| POST | `{pk}/rework/` | Owner / host | `archived → draft` |

---

### Analytics — `/api/v1/analytics/`

Read-only partner-institution analytics dashboard. Its own app; owns no models — aggregates over courses, webinars, enrollments, certificates, and the expert roster. All endpoints require a **verified** partner institution (`IsVerifiedPartnerInstitution`) and are scoped to the caller's own institution (no resource id in the URL → the only no-access response is 403). See [docs/architecture/20-analytics-dashboard.md](docs/architecture/20-analytics-dashboard.md) and [docs/api-testing/postman-analytics.md](docs/api-testing/postman-analytics.md).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `partner/summary/` | Dashboard KPI cards: courses, enrollments, certificates, webinars, roster, engagement score. Revenue disabled (no payments model); webinar attendance flagged off until the live-day join flow ships |
| GET | `partner/enrollments/trend/` | Enrollment time series (`?granularity=monthly\|weekly&periods=N`, zero-filled contiguous) |
| GET | `partner/webinars/trend/` | Webinar-registration time series |
| GET | `partner/certificates/trend/` | Certificate-issuance time series |
| GET | `partner/top-courses/` | Ranked courses (`?sort=enrollments\|rating\|completion&limit=N`) |
| GET | `partner/experts/performance/` | Per-expert outcome metrics for the whole active roster (courses credited, content authored, avg rating, enrollments, completion, certificates, webinars hosted, last-active) |
| GET | `partner/experts/{expert_id}/performance/` | One expert (numeric id → 404 if not an active affiliate) |

> Expert performance credits a course to **every** instructor + its creator (co-taught courses count toward each — per-expert sums can exceed institution totals; stated in the payload's `attribution`).

### Payments — `/api/v1/payments/`

SSLCommerz hosted-checkout payments for courses **and** webinars (sandbox via `SSLCOMMERZ_SANDBOX`). Currency BDT. Learner-gated except the gateway callbacks.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `checkout/` | Open a gateway session for a paid course or webinar (body: exactly one of `{course_slug}` / `{webinar_slug}`) → `{gateway_url, order_id, tran_id, item_type, amount, currency}` |
| POST | `ipn/` | SSLCommerz server-to-server notification (unauthenticated; validated server-side) |
| GET/POST | `success/` | Gateway success redirect → validates + finalizes → 302 to frontend success page |
| GET/POST | `fail/` | Gateway fail redirect → marks order failed → 302 to frontend |
| GET/POST | `cancel/` | Gateway cancel redirect → marks order cancelled → 302 to frontend |
| GET | `orders/` | Caller's own orders (`?status=` filter, paginated) |
| GET | `orders/{id}/` | One own order (cross-user → 404) |

> Payment state is never taken from redirect/IPN bodies — the backend re-queries the SSLCommerz Validation API and verifies amount/currency/tran_id/store_id against the order snapshot before marking anything `paid`. The `paid` order + access grant (PAID enrollment or webinar registration) are created atomically; double IPN / redirect races are idempotent.

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

# Celery worker (-Q covers the default queue + the routed notifications queue)
celery -A career_college_backend worker --loglevel=info --pool=solo -Q celery,notifications  # Windows
celery -A career_college_backend worker --loglevel=info -Q celery,notifications              # macOS/Linux

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
│   ├── tasks.py                   Celery email tasks (async OTP + expert credentials)
│   └── services/                  OAuth + expert (institution-onboarded) provisioning helpers
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
│   ├── services/                  Curriculum, enrollment, learner-consumption, grading, code-runner helpers
│   │   ├── learner_service.py     Curriculum, lecture/quiz/assignment/coding consumption + submission flows
│   │   ├── code_runner.py         Docker sandbox + per-language batched harness (one container per submission)
│   │   ├── assignment_grading.py  RubricGrader for the assignment auto-grader
│   │   ├── enrollment_service.py  Catalog filtering + `recalculate_progress` (lecture/quiz/assignment/coding rollup)
│   │   ├── certificate_service.py `issue_certificate`, `get_certificate_for_learner`, `get_certificate_by_uid`
│   │   └── review_service.py      `ReviewError`, review upsert/delete, vote flip, `_recalculate_course_avg`
│   ├── selectors.py               Reusable query helpers
│   ├── signals.py                 Domain event hooks (enrollment progress updates)
│   ├── tasks.py                   Celery tasks (video transcoding, assignment grading, coding run/submit, zombie reaper)
│   ├── transcoding.py             FFmpeg transcoding routines
│   └── management/commands/
│       └── seed_course_categories.py
├── id_verification/               Identity verification workflow
├── messaging/                     Generalized 2-party messaging (learner↔instructor, co-instructor, institution↔expert)
│   ├── models.py                  Conversation (conversation_type), ConversationParticipant (per-user cursor), Message
│   ├── services/messaging_service.py  Per-type send-gate dispatch, start_conversation, send/read, unread counts, WS push + notify
│   ├── all_views/conversation_views.py  REST endpoints (list, create, detail, send, read, unread-count)
│   └── tests/                     Model, service, view, and conversation-type tests
├── notifications/                 In-app notification feed + email preferences + dispatcher
├── realtime/                      ASGI WebSocket layer
│   ├── consumers.py               PlatformConsumer — multiplexes streams, routes channel events
│   └── streams/                   Per-stream handlers (notifications_stream, messaging_stream)
├── webinars/                      Institution-owned live webinars
│   ├── all_models/                Webinar (status machine) + WebinarRegistration
│   ├── all_serializers/           Authoring, catalog (no meeting_url), registrant (with meeting_url)
│   ├── all_views/                 webinar / status / host / catalog / registration views
│   ├── services/                  webinar_service (host + speakers), registration_service (capacity lock)
│   └── all_tests/                 End-to-end flow, editing scope, transitions, capacity, notifications
├── core/                          Shared: permissions, pagination, middleware
├── docs/architecture/             19 architecture design documents + guide README
├── docs/api-testing/              Postman/wscat testing guides (certificate, coinstructor, messaging, notifications, partner-institution, …)
├── docs/future_implementations/   Design notes for planned features (auto-caption generation)
├── scripts/                       Manual smoke tests (real Docker; not in test suite)
│   ├── smoke_code_runner.py       End-to-end Run for all 4 languages
│   └── smoke_runtime_error.py     Per-test try/except isolation check
├── templates/                     Email templates
├── manage.py
├── requirements.txt
├── .env.example
├── CLAUDE.md
├── POSTMAN_TESTING_GUIDE.md
└── FRONTEND_ERROR_RESPONSE_FORMAT.md
```

---

## Architecture Notes

- **Custom user model** — `authentication.User`, email-based (no username field). `user_type` field: `learner`, `instructor`, `partner_institution`, `admin`.
- **Transactional email** — OTP (registration / resend / password reset) and institution-onboarded expert credentials are sent asynchronously via Celery tasks in `authentication/tasks.py`; views enqueue and return immediately while the worker handles SMTP + retries. A 503 from register/resend means the broker enqueue failed, not the SMTP send. Institution-onboarded experts are created with `is_email_verified=True` + a preset password emailed to them — no OTP step, loginable immediately.
- **SectionContent** — single source of truth for curriculum ordering within a section. Holds a `GenericForeignKey` to `Lecture`, `Quiz`, `CodingExercise`, or `Assignment`. Deleting any content object cascades and removes its `SectionContent` slot.
- **Status transitions** — `NidusCourse.transition_to()` is the only entry point for status changes. Never set `status` directly on a course instance.
- **Video pipeline** — raw upload → Celery → FFmpeg → 5 HLS renditions. `VideoAsset.status` tracks progress. Only one active asset per lecture at a time.
- **Identity verification** — instructors must have `InstructorProfile.is_verified = True` before any course authoring endpoint is accessible (`IsVerifiedInstructor` permission).
- **Enrollment** — `Enrollment` enforces one record per learner/course, supports soft-unenroll (`is_active=False`), and stores denormalized `progress_percent` for dashboard performance.
- **Permissions** — all custom permission classes live in `core/permissions.py`. Never define them inside app directories. `IsLearnerUser` gates learner-only enrollment/dashboard endpoints.
- **`solution_code`** on `CodingExerciseLanguageConfig` and **`model_answer`** on `AssignmentQuestion` are instructor-only and must never appear in learner-facing serializers.
- **Hidden test cases** (`CodingTestCase.is_hidden = True`) are for grading only. Per-row data is omitted entirely from learner responses; aggregate counts still include them.
- **Coding runner contract**: learner code must define a top-level `solve(...)` function taking one string argument. The harness substitutes test inputs through `INPUT_{i}` env vars, captures per-test stdout/stderr/runtime via sentinel markers, and aggregates results Python-side. Single container per submission — see [docs/architecture/09-coding-exercises.md](docs/architecture/09-coding-exercises.md).
- **Coding execution sandbox**: one Docker container per submission with `runtime='runsc'` (gVisor; configurable via `RUNNER_RUNTIME`), `network_disabled`, 128 MB RAM, 0.5 CPU (`nano_cpus=500_000_000`), `pids_limit=64`, `ulimits` (fsize 10 MB, nproc 64, nofile 128, cpu 10 s), read-only root FS, 32 MB tmpfs at `/tmp`, all capabilities dropped, `no-new-privileges`. Wall-clock budget enforced via `container.wait(timeout=...)` + `container.kill()`. Demo-only — Docker-out-of-Docker; the daemon socket is shared with the host.
- **Coding submission idempotency**: `evaluate_coding_submission_task` is `acks_late=True` and short-circuits on terminal status, so worker-death redelivery is safe. A Celery-beat reaper (`reap_stuck_coding_submissions_task`, 60 s) flips `queued`/`grading` rows older than 5 min to `error`.
- **Certificates** — issued automatically when `progress_percent` reaches 100% for the first time (`recalculate_progress` → `transaction.on_commit` → `_issue_certificate_and_notify`). `Certificate` is identified by a UUID4 (non-guessable); `issue_certificate` uses `get_or_create` so Celery redelivery is idempotent. PDF generated on-the-fly by reportlab (`courses/certificate_pdf.py`); no file stored on disk.
- **Reviews & ratings** — `CourseReview` is one-per-enrollment (enforced by `OneToOneField(enrollment)`). `ReviewVote` tracks helpful/not-helpful per reviewer. `avg_rating` and `review_count` are denormalized onto `NidusCourse` (updated via `transaction.on_commit` after every review write) so catalog sort/filter (`?sort=rating`, `?rating_min=`, `?min_reviews=`) stays a single-table scan. Vote flips use `select_for_update` + `F()` expressions for atomicity.
- **Messaging** — a `Conversation` is a role-neutral 2-party thread selected by `conversation_type` (`learner_instructor` | `co_instructor` | `institution_expert`); the two parties live in `ConversationParticipant`, each row carrying that user's read cursor. The send-gate is dispatched by type in `messaging_service` (`_assert_send_permission` at send, `_validate_new_conversation` at create) and enforced identically on the REST and WebSocket paths. Marking a thread read is a single UPDATE of the caller's participant cursor. Pair uniqueness is `(conversation_type, course, participant_key)`. After a message commits, a `new_message` event is pushed **only to the recipient's** group (the sender already has it), plus a `message.received` notification via `transaction.on_commit`. Institution announcements (one-to-many) are notification fan-out, not conversations (unbuilt — see `docs/future_implementations/INSTITUTION_MESSAGING.md` §8).
- **Realtime / WebSocket** — a single ASGI `PlatformConsumer` at `/ws/` multiplexes per-feature streams (`{"stream": "...", "payload": {...}}`); JWT is passed as a `?token=` query param and validated on connect. Cross-process delivery uses the Redis channel layer (`group_send` to `messaging_user_{id}` / notification groups). Adding a stream = register a handler class in `realtime/streams/`.
- **Webinars** — institution-owned live sessions that link out to an external provider (`meeting_url`), not a curriculum tree. Three presenter roles: `host_expert` (FK, publishes), `institutional_speakers` (M2M, credit-only), `guest_speakers` (JSON, no account). Three-state machine (`draft → published → archived`) with **no approval gate** — the assigned host publishes directly via `transition_to()`. GET is owner-or-host; PATCH is institution-only (host reads but cannot edit). `meeting_url` is registrant-only — enforced by dedicated serializers (catalog omits it, registrant serializer includes it), never conditional stripping. Registration enforces capacity under a `select_for_update` lock on the webinar row. `WEBINAR_PUBLISHED` / `WEBINAR_REGISTERED` notifications each need four-point wiring (event type, builder, `EVENT_TO_CATEGORY`, `_EVENT_TEMPLATE_MAP`).

---

## Documentation

### General

| File | Contents |
|------|----------|
| [POSTMAN_TESTING_GUIDE.md](POSTMAN_TESTING_GUIDE.md) | Complete API testing guide — auth, courses, learner consumption (lectures, quizzes, assignments, coding), certificates, reviews & ratings |
| [FRONTEND_ERROR_RESPONSE_FORMAT.md](FRONTEND_ERROR_RESPONSE_FORMAT.md) | Error response shape spec |
| [docs/future_implementations/AUTO_CAPTION_GENERATION.md](docs/future_implementations/AUTO_CAPTION_GENERATION.md) | Design notes for a planned auto-caption feature (not yet built) |
| [docs/api-testing/postman-certificate.md](docs/api-testing/postman-certificate.md) | Postman testing guide for the certificate issuance + verify/download endpoints |
| [docs/api-testing/postman-coinstructor-invite.md](docs/api-testing/postman-coinstructor-invite.md) | Postman testing guide for the co-instructor invite/accept flow |
| [docs/api-testing/postman-course-owner-protection.md](docs/api-testing/postman-course-owner-protection.md) | Postman testing guide for course-owner vs co-instructor roster protection |
| [docs/api-testing/postman-messaging.md](docs/api-testing/postman-messaging.md) | Postman / wscat testing guide for the messaging REST + WS endpoints |
| [docs/api-testing/postman-notification-system.md](docs/api-testing/postman-notification-system.md) | Postman testing guide for the notification feed + email preferences |
| [docs/api-testing/postman-partner-institution.md](docs/api-testing/postman-partner-institution.md) | Postman testing guide for partner-institution verification, expert management, course creation + roster assignment |
| [docs/api-testing/postman-expert-course-editing.md](docs/api-testing/postman-expert-course-editing.md) | Postman testing guide for an institution-assigned expert logging in and editing/submitting their course |
| [docs/api-testing/postman-webinars.md](docs/api-testing/postman-webinars.md) | Postman testing guide for webinar authoring, host assignment, publish, catalog, and learner registration |
| [docs/api-testing/postman-analytics.md](docs/api-testing/postman-analytics.md) | Postman testing guide for the partner-institution analytics dashboard (summary, trends, top-courses) |
| [CLAUDE.md](CLAUDE.md) | AI assistant coding instructions |

### Architecture (`docs/architecture/`)

Read in order; [docs/architecture/README.md](docs/architecture/README.md) is the guide map.

| File | Contents |
|------|----------|
| [README.md](docs/architecture/README.md) | Backend architecture guide — recommended reading order + scope |
| [01-system-overview.md](docs/architecture/01-system-overview.md) | Architecture diagram, project layout, request lifecycle, design patterns |
| [02-auth-and-accounts.md](docs/architecture/02-auth-and-accounts.md) | Registration, OTP, JWT, OAuth flows |
| [03-profiles.md](docs/architecture/03-profiles.md) | Profile models, auto-creation signal, public/private endpoints |
| [04-courses-and-curriculum.md](docs/architecture/04-courses-and-curriculum.md) | Course models, SectionContent ordering, reorder algorithm |
| [05-lectures-and-video-pipeline.md](docs/architecture/05-lectures-and-video-pipeline.md) | Video upload, FFmpeg transcoding, HLS pipeline, WatchProgress |
| [06-quizzes.md](docs/architecture/06-quizzes.md) | Quiz authoring, attempt models, learner submission flow |
| [07-id-verification.md](docs/architecture/07-id-verification.md) | Identity verification state machine, admin review |
| [08-core-infrastructure.md](docs/architecture/08-core-infrastructure.md) | Permissions, pagination, Celery tasks, JWT config, logging |
| [09-coding-exercises.md](docs/architecture/09-coding-exercises.md) | Coding exercise authoring + Run/Submit execution + Docker sandbox |
| [10-assignments-crud.md](docs/architecture/10-assignments-crud.md) | Assignment CRUD + async auto-grading + RubricGrader |
| [11-course-lifecycle.md](docs/architecture/11-course-lifecycle.md) | Course status state machine, completeness checks, admin review |
| [12-enrollment.md](docs/architecture/12-enrollment.md) | Enrollment, progress calculation, learner consumption endpoints |
| [13-multi-instructor-collaboration.md](docs/architecture/13-multi-instructor-collaboration.md) | Owner vs co-instructor roles, roster protection, guard_owner utility |
| [14-certificate-system.md](docs/architecture/14-certificate-system.md) | Completion certificate issuance flow, PDF generation, public share URLs |
| [15-review-rating-system.md](docs/architecture/15-review-rating-system.md) | Review/rating data model, vote atomicity, denormalized catalog fields, access policy |
| [16-notification-system.md](docs/architecture/16-notification-system.md) | Notification dispatcher, event types, WebSocket delivery |
| [17-messaging-system.md](docs/architecture/17-messaging-system.md) | Messaging data model, REST + WebSocket protocol, unread semantics, frontend client contract |
| [18-partner-institutions.md](docs/architecture/18-partner-institutions.md) | Institution verification, expert onboarding, departments, course creation + roster assignment |
| [19-webinars.md](docs/architecture/19-webinars.md) | Institution-owned webinars — presenter roles, publish state machine, catalog + registration, notification wiring |
| [20-analytics-dashboard.md](docs/architecture/20-analytics-dashboard.md) | Partner-institution analytics — metrics, institution-scoping, query strategy, revenue/attendance caveats |
