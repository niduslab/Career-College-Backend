# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Keep this file current.** Whenever you rename a symbol, add a new pattern, change a convention, or introduce an architectural rule, update the relevant section of this file in the same change. Stale guidance is worse than no guidance.

## Project Overview

A Django REST Framework backend for a course marketplace platform (Coursera-like). Users can be learners, instructors, partner institutions, or admins. Instructors **and verified partner institutions** create courses with mixed content (lectures, quizzes, coding exercises), upload videos that are async-transcoded to HLS, and must pass identity verification before publishing. Partner institutions own the course (`created_by`) and add instructors; instructors can edit content but not the roster.

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

Celery worker (required for video transcoding **and** assignment auto-grading):
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
| `messaging` | `/api/v1/messaging/` | Learner ↔ instructor direct messaging (REST + WebSocket); send-gate in service layer |
| `notifications` | `/api/v1/notifications/` | In-app notification feed, email preferences, `dispatch()` API |
| `realtime` | `/ws/` | ASGI `PlatformConsumer` multiplexing the `notifications` and `messaging` WebSocket streams |
| `core` | — | Shared permissions, pagination, middleware |

### Messaging

`messaging/` — one `Conversation` per `(learner, instructor, course)` triad; **only learners initiate**, either party replies. The send-gate (learner active enrollment / instructor still on course) lives in `messaging/services/messaging_service.py` → `send_message()` and is enforced identically on the REST and WebSocket paths — never duplicate it in a view or consumer. Unread state is two timestamp cursors (`learner_last_read_at` / `instructor_last_read_at`), not per-message flags: marking read is one UPDATE. After a message commits, `_push_ws_and_notify` pushes a `new_message` channel event **only to the recipient's** `messaging_user_{id}` group (the sender already has it via the `message_sent` ack or 201 body — pushing to both causes duplicate delivery) and dispatches `MESSAGE_RECEIVED`. Two unread metrics, same rule: `get_unread_counts()` (per-conversation message counts) and `get_unread_conversation_count()` (number of threads with ≥1 unread); they always agree. Numeric IDs → 404 on no-access. See `docs/architecture/17-messaging-system.md`.

### Realtime / WebSocket

`realtime/` — a single ASGI `PlatformConsumer` at `/ws/` multiplexes per-feature streams via `{"stream": "<name>", "payload": {...}}`. JWT is passed as a `?token=` query param and validated on connect (close `4001` on failure). Cross-process delivery uses the Redis channel layer (`group_send`). Add a stream by registering a handler class in `realtime/streams/` and wiring it into `_STREAM_HANDLER_CLASSES` / `_CHANNEL_EVENT_DISPATCH` in `realtime/consumers.py`. Stream handlers run async — wrap ORM calls in `database_sync_to_async`.

### Custom User Model

`authentication/models.py` — email-based (no username field), with `user_type` field: `learner`, `instructor`, `partner_institution`, `admin`. On creation, a signal (`authentication/signals.py`) auto-creates the matching profile (`LearnerProfile`, `InstructorProfile`, or `PartnerInstitutionProfile`). `AUTH_USER_MODEL = 'authentication.User'`.

### Course Content Ordering: SectionContent

The `SectionContent` model (in `courses/`) is the **single source of truth for ordering** within a section. It holds a `GenericForeignKey` that points to a `Lecture`, `Quiz`, or `CodingExercise`. When adding new content types, create the model and then create a `SectionContent` row linking it into the section — do not add ordering directly to content models. Each content model must have a `GenericRelation` to `SectionContent` so that deleting the object cascades and removes its curriculum slot automatically. Reordering logic lives in `courses/services/section_service.py` → `reorder_section_content()`.

### Content Authorship: AuthoredModel

`AuthoredModel` (abstract, in `courses/all_models/course_models.py`, extends `TimestampedModel`) adds `created_by` + `last_edited_by` (both FK→User, `SET_NULL`, nullable, `related_name='+'`) to every content model an expert/instructor authors: `CourseSection`, `SectionContent`, `Lecture`, `Quiz`, `Assignment`, `CodingExercise`. Powers partner-institution monitoring — which expert authored / last touched a content row (SRS 7.2.1, 7.7.3). **New authored content models must inherit `AuthoredModel`, not `TimestampedModel`.**

Stamping is centralised in `courses/utils.py` → `save_authored(serializer, user, **extra)`: sets `created_by` only on create (no bound instance), `last_edited_by` on every save, and passes extra kwargs through to `serializer.save()`. Every content create/update view path uses it. Non-serializer create paths (`Assignment`/`CodingExercise` `objects.create`, `create_section_content_for_object(..., created_by=user)`, `assignment_service.update_assignment`) set the fields directly. `QuizQuestion`, `QuizAnswer`, `CodingExerciseLanguageConfig`, `CodingTestCase` are **not** `AuthoredModel` (sub-rows of an already-authored parent). Never trust a client-supplied author — always stamp from `request.user`.

The read serializers (`CourseSectionSerializer`, `LectureSerializer`, `SectionContentSerializer`, `QuizSerializer`, `CodingExerciseSerializer`, `AssignmentSerializer`) expose `created_by` + `last_edited_by` as nested `InstructorBriefSerializer` (`id`, `full_name`, `email`), read-only. List loaders `select_related('created_by', 'last_edited_by')` (`get_course_sections`, `get_section_lectures`, and the `SectionContent` list query) so the author fields don't N+1.

### Course Detail Endpoints

Four distinct course detail surfaces, split by audience and concern. Do not collapse them into one conditional endpoint:

| Endpoint | Audience | Purpose |
|---|---|---|
| `GET /catalog/<slug>/` (`CatalogCourseDetailView`, `AllowAny`) | Guests / unenrolled | Marketing page — course metadata + full curriculum outline (titles, durations) + preview lecture HLS URLs only for `Lecture.is_preview=True`. Stays as a one-shot tree because catalog browsing is a guest workflow and SEO benefits from a single page render. |
| `GET /my-courses/<slug>/` (`MyCoursesDetailView`, `IsEmailVerified`) | Enrolled learner OR course's own instructor | **Slim metadata only**: title, description, instructors, learning objectives/prerequisites/audiences, totals, plus the caller's enrollment block (progress %, completed_at, last_accessed_at) and `is_instructor` flag. Curriculum tree and per-item content **do not** live here — fetch them from `/learn/<slug>/curriculum/` and `/learn/<thing>/<id>/`. |
| `GET /learn/<slug>/curriculum/` (`LearnerCurriculumView`) | Same as above | Sidebar curriculum outline (sections + items, lightweight). See *Learner Consumption Endpoints* below. |
| `GET /<int:pk>/` (`CourseDetailView`, `IsVerifiedCourseCreator`) | Course's own instructor OR partner institution owner | Authoring/edit surface (GET + PATCH metadata). Curriculum edits flow through `/sections/`, `/lectures/`, `/contents/`. |

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
| `GET /learn/assignments/<int:assignment_id>/` | `LearnerAssignmentDetailView` | Assignment + questions for the attempt UI. `model_answer` and `rubric` are never declared on the learner serializer (absence beats conditional removal). Includes the caller's `latest_submission` summary when one exists. |
| `POST /learn/assignments/<int:assignment_id>/submit/` | `LearnerAssignmentSubmitView` | Creates an `AssignmentSubmission(status='submitted')` + one `AssignmentSubmissionAnswer` per question. `AssignmentSubmission.max_score` snapshots `assignment.total_score` (the instructor-declared total); each answer's `max_score` snapshots its `question.points`; each answer's `rubric_snapshot` freezes `question.rubric`. Returns **`202 Accepted`** with `status='submitted'`; the Celery task `grade_assignment_submission_task` runs the `RubricGrader` and transitions the row to `passed` / `failed` / `grading_failed`. `422` if an in-flight submission already exists for the `(user, assignment)` pair. |
| `GET /learn/assignments/submissions/<int:submission_id>/` | `LearnerAssignmentSubmissionDetailView` | Learner sees own only. Returns `status`, per-answer `score` / `criterion_results` / `feedback`. The instructor's `model_answer` is included per-question **only when** `status in (passed, failed)` — hidden during `submitted` / `grading` / `grading_failed`. |
| `POST /learn/assignments/submissions/<int:submission_id>/retry/` | `LearnerAssignmentSubmissionRetryView` | Re-enqueue grading for a submission stuck in `grading_failed`. Reuses the same row (clears `grading_error`, flips `status` back to `grading`, re-dispatches the Celery task) so `submitted_at` + historical correlation stay correct. Any other current status → `422`; another learner's submission → `404`. |

Permission model:
- `GET` endpoints accept *enrolled learner OR the course's own instructor* (preview matching `MyCoursesDetailView`). Slug-based curriculum → 403 for unenrolled; numeric-ID endpoints → 404 (existence not leaked).
- `POST /progress/` and `POST /submit/` are `IsLearnerUser`-gated; instructors get `403` (preview must not pollute progress or attempt history). Unenrolled learners get `404`.

Data loaders live in `courses/services/learner_service.py`:
- `resolve_course_access(user, course)` → `(is_instructor, enrollment_or_none)`. Use this in any new learner endpoint that needs the same access policy.
- `load_learner_curriculum(course, user, is_instructor)` — bulk-loads `.only(...)` lightweight fields and a single `WatchProgress` query for completion markers.
- `get_consumption_lecture(user, lecture_id)` / `get_quiz_for_consumption(user, quiz_id)` — fetch + verify access in one call. Raise `Lecture.DoesNotExist` / `Quiz.DoesNotExist` on missing-or-no-access.
- `upsert_watch_progress(user, lecture, ...)` — never touches the enrollment row directly; the signal handles recalc.
- `submit_quiz_attempt(user, quiz, answers_payload)` — atomic: creates the `QuizAttempt` + per-question `QuizAttemptAnswer` rows, computes score from the live answer key, and **caches `is_correct` onto each attempt row** so historical attempts stay frozen against later instructor edits.
- `get_assignment_for_consumption(user, assignment_id)` — same access shape as the quiz loader; questions are prefetched ordered by `position`. Raises `Assignment.DoesNotExist` on missing-or-no-access.
- `submit_assignment(user, assignment, answers_payload, enrollment)` — atomic: validates the in-flight constraint (belt-and-braces on top of the Postgres partial unique index), creates the parent + bulk-creates answer rows with `rubric_snapshot` and `max_score` copied from each `AssignmentQuestion`, and **dispatches `grade_assignment_submission_task` via `transaction.on_commit`** so a rolled-back transaction can't leak a phantom Celery task into the queue. Raises `AssignmentSubmissionError(http_status=422)` when an in-flight submission already exists.
- `get_learner_assignment_submission(user, submission_id)` — fetch + verify ownership in one call. Raises `AssignmentSubmission.DoesNotExist` when missing OR owned by someone else (numeric ID → 404, never 403).
- `retry_assignment_grading(user, submission_id)` — atomic + `select_for_update`. Permits retry only when status is `grading_failed`; flips status to `grading`, clears `grading_error`, re-dispatches the Celery task on commit. Anything else → `AssignmentSubmissionError(422)`.

Serializers live in `courses/all_serializers/learner_serializers.py`. `build_quiz_attempt_result(attempt)` and `build_assignment_submission_result(submission)` are functions (not serializer classes) because their conditional-presence rules ("show correct answer only when wrong", "reveal `model_answer` only when graded") are awkward to express with DRF field declarations and trivial in plain Python; centralising them means every caller gets identical behaviour.

Auto-grading lives in `courses/services/assignment_grading.py`. `RubricGrader().grade(answer_text, rubric_snapshot, max_score) → (score, criterion_results, feedback)` runs deterministic per-criterion matchers (`keyword`, `regex`, `min_length`, `max_length`, `any_of`, `all_of`). Adding a new criterion type is additive: register a matcher in `_MATCHERS` (grader) and a value-validator in `_RUBRIC_CRITERION_VALUE_VALIDATORS` (authoring serializer). The grader is defensive — an unknown type or a matcher that raises is recorded as a miss, never crashes the grading task.

Grading runs out-of-band via `grade_assignment_submission_task` in `courses/tasks.py`, mirroring the video transcoding pattern. The task is decorated `acks_late=True` so a worker death mid-task causes the broker to redeliver; the next invocation either resumes or short-circuits because the status is already terminal. On final retry exhaustion the task marks the submission `grading_failed` with a truncated error message — the learner can re-enqueue via the `/retry/` endpoint.

Coding-exercise learner runtime is **built** (Docker-backed). Endpoints (`courses/urls.py`), all on numeric IDs → 404 on no-access:

| Endpoint | View | Purpose |
|---|---|---|
| `GET /learn/coding-exercises/<id>/` | `LearnerCodingExerciseDetailView` | Exercise detail — starter code per language, **visible** test cases only (hidden filtered out), caller's latest-submission summary. |
| `POST /learn/coding-exercises/<id>/run/` | `LearnerCodingRunView` | Transient run against **visible** tests only. Dispatches `evaluate_coding_run_task`; nothing persisted. Returns a `task_id` to poll. |
| `POST /learn/coding-exercises/<id>/submit/` | `LearnerCodingSubmitView` | Persisted submission against **all** tests (incl. hidden). Creates `CodingSubmission`, dispatches `evaluate_coding_submission_task`. Returns a `task_id`. |
| `GET /learn/coding-exercises/tasks/<task_id>/` | `LearnerCodingTaskStatusView` | Poll the Celery `AsyncResult` for a run/submit. |
| `GET /learn/coding-exercises/submissions/<id>/` | `LearnerCodingSubmissionDetailView` | Learner sees own only. Per-test results; hidden tests' input/expected are snapshotted but masked. |
| `POST /learn/coding-exercises/submissions/<id>/retry/` | `LearnerCodingSubmissionRetryView` | Re-enqueue a submission stuck in `error`. Other statuses → `422`. |

Loaders in `courses/services/learner_service.py`: `get_coding_exercise_for_consumption`, `run_coding_exercise`, `submit_coding_exercise`, `get_learner_coding_submission`, `retry_coding_submission`. `CodingSubmissionError(http_status=...)` mirrors `AssignmentSubmissionError`. Execution: `CodeRunner` (Docker, one container per submission) in `courses/services/code_runner.py`; tasks `evaluate_coding_run_task` / `evaluate_coding_submission_task` in `courses/tasks.py`; `reap_stuck_coding_submissions_task` (Celery beat) recovers stuck rows. `CodingSubmission.status`: `queued → grading → passed | failed | error`. Learner serializers in `courses/all_serializers/learner_serializers.py` (`LearnerCodingExerciseDetailSerializer`, `LearnerCodingSubmissionSerializer`, etc.) — dedicated classes that never declare `solution_code` or hidden-test bodies.

`recalculate_progress()` in `enrollment_service.py` counts lecture completion (`WatchProgress.is_completed=True`), quiz attempts (≥1 `QuizAttempt` per quiz), and assignment submissions in the `passed` state (`AssignmentSubmission.status='passed'`). Lectures recalc via the `WatchProgress` post_save signal; `submit_quiz_attempt` calls `recalculate_progress(enrollment)` directly at the end of its transaction; `grade_assignment_submission_task` schedules `recalculate_progress` via `transaction.on_commit` only when the final status is `passed`. Coding-exercise completion **counts** — `recalculate_progress` (`enrollment_service.py:394-409`) tallies distinct exercises with a `CodingSubmission.status='passed'`; `evaluate_coding_submission_task` triggers the recalc on pass.

`upsert_watch_progress` enforces two server-side invariants the client cannot override: `watched_seconds` is clamped to the active `VideoAsset.duration_seconds` (HLS players legitimately overshoot by a fraction, so we cap rather than reject), and if the clamped cursor lands at duration, `is_completed` is forced to `True` (the video has functionally ended regardless of what the client declared). Article lectures have no duration; `watched_seconds` is forced to `0`.

### Learner-Safe Serialization

The following fields **must remain instructor-only** in any learner-facing response:

| Field | Audience | Status |
|---|---|---|
| `Lecture.stream_master_playlist` (full HLS URL) | Exposed on `/learn/lectures/<id>/` for any caller with access; on `/catalog/` only when `Lecture.is_preview=True` | Done |
| `QuizAnswer.is_correct` | Instructor only (omit from learner payload pre-submit; score server-side; reveal the correct answer in the post-submit response only for wrong questions) | Done — `_LearnerQuizAnswerOptionSerializer` simply doesn't declare it; `build_quiz_attempt_result` controls reveal-on-wrong |
| `AssignmentQuestion.model_answer` | Instructor only (omit from learner attempt payload; reveal in submission detail only when `status in (passed, failed)`) | Done — `_LearnerAssignmentQuestionSerializer` doesn't declare it; `build_assignment_submission_result` controls reveal-on-graded; authoring `AssignmentQuestionSerializer.to_representation` strips it for non-instructors |
| `AssignmentQuestion.rubric` | Instructor only (grading-rule definition; never exposed to learners pre- or post-submit, including the snapshotted copy on submission rows) | Done — `_LearnerAssignmentQuestionSerializer` doesn't declare it; authoring serializer strips it for non-instructors; `rubric_snapshot` on `AssignmentSubmissionAnswer` is consumed by the grader, never serialized to learners |
| `CodingExerciseLanguageConfig.solution_code` | Instructor only | Done — `_LearnerCodingLanguageConfigSerializer` declares only starter code, never `solution_code` |
| `CodingTestCase.is_hidden` + hidden test cases | Instructor only (filter hidden cases out of learner payload) | Done — `_LearnerCodingTestCaseSerializer` serves only visible tests; hidden test bodies on submission results are masked |

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

### Institution Verification State Machine

`InstitutionVerification` (also in `id_verification/`, mirrors `IdentityVerification`) verifies a **partner institution's** credentials. FK is to `PartnerInstitutionProfile` (not `User`). States: `draft → submitted → under_review → approved | rejected | action_required` (no `expired`). `transition_to()` is the single entry point; approval calls `_mark_institution_verified()` which sets `PartnerInstitutionProfile.is_verified = True` (and `is_active = True`). `clean()` enforces `institution.user.user_type == 'partner_institution'`. Required-before-submit: `registration_number`, `issuing_authority`, `accreditation_document`. Institution-facing endpoints `/api/v1/verification/institution/...` (create/update/submit/my-list/my-detail, gated `IsEmailVerified` + a `user_type=='partner_institution'` guard, **not** `IsVerifiedPartnerInstitution` — verification is the gate). Admin endpoints `/api/v1/verification/admin/institution/...`. Submit notifies admins (`INST_VERIFICATION_SUBMITTED`); decisions notify the institution (`INST_VERIFICATION_APPROVED/REJECTED/ACTION_REQ`) via `transaction.on_commit`.

### Partner Institution: Experts & Course Roster

**Expert onboarding (auto-provision).** A verified institution onboards experts via `authentication/services/expert_service.py` → `provision_expert()`: creates a `User(user_type='instructor')` (the profile signal fires), then sets `InstructorProfile.affiliated_institution`, `onboarding_source='institution'`, `affiliation_status='active'`, `affiliated_at`, **`is_verified=True`** (the institution vouches — institution-onboarded experts skip their own `IdentityVerification` and can author immediately) and **`is_email_verified=True`** on the `User` (no OTP email-ownership proof needed). The account is created with a **preset password** (`secrets.token_urlsafe(9)`), emailed to the expert (login email + password) via `send_expert_credentials_email_task` enqueued on `transaction.on_commit` — the expert can log in immediately, no OTP step. The plaintext password is passed as a task arg and is deliberately kept out of the `EXPERT_ONBOARDED` notification payload (`skip_email=True`) so it is never persisted in `Notification.data`. Deactivation (`set_expert_active(..., active=False)`) flips `affiliation_status='removed'` **and** `is_verified=False` so a removed expert can no longer author. Endpoints `/api/v1/auth/partner/experts/` (list/create) and `/<id>/` (detail/patch), gated `IsVerifiedPartnerInstitution`; every query scoped to the caller's own institution; numeric-ID detail → 404 on no-access.

**Departments.** An institution defines its own departments (`Department` model in `authentication/models.py`, owned by `PartnerInstitutionProfile` via `institution` FK, name unique per institution case-insensitively). `InstructorProfile.department` is a nullable FK to `Department` (`on_delete=SET_NULL`, rename-safe). CRUD lives in `authentication/services/department_service.py`; endpoints `/api/v1/auth/partner/departments/` (list/create) and `/<id>/` (detail/patch/delete), gated `IsVerifiedPartnerInstitution`, scoped to the caller's institution, numeric-ID → 404. **DELETE soft-deactivates** (`is_active=False`) — assigned experts keep their FK; inactive departments are excluded from the default list. An expert's `department_id` (on `provision_expert`/`update_expert`) is validated by `resolve_expert_department()` to be an **active department of the expert's own institution** — a foreign/unknown id raises `ExpertError(422)` (mirrors the roster cross-institution rule).

**Course creation.** Partner institutions create courses through the same `CourseCreateAPIView` as instructors (`IsVerifiedCourseCreator`). `NidusCourseCreateUpdateSerializer.create()` already sets `partner_institution` and skips `instructors.set([self])` for partner creators. `NidusCourse.clean()` permits `created_by.user_type in ('instructor', 'partner_institution')` — **never narrow this back to instructor-only.**

**Roster assignment (direct add, no invite/accept).** Partner institutions own the roster and add experts directly via `courses/services/institution_course_service.py` → `add_course_instructor()` / `remove_course_instructor()`, exposed at `POST/DELETE /api/v1/courses/<pk>/institution-instructors/[<expert_user_id>/]` (`InstitutionCourseInstructorView`, `IsVerifiedPartnerInstitution`). Only an **active affiliated expert** of the owning institution may be added, and only while the course `is_editable()`. This is distinct from the instructor `CourseInstructorInvite` flow (which requires an accept step) — do not conflate them. Assigned experts edit content through the existing `CourseDetailView` / content endpoints (`Q(instructors=user) | Q(created_by=user)` already covers them). `InstitutionCourseError(message, http_status)` mirrors `InviteError`.

### Permissions (core/permissions.py)

Custom DRF permission classes used across views:

- `IsPlatformAdmin` — `is_staff` or `user_type == admin`; used by admin-only actions like course review
- `IsEmailVerified` — gates most authenticated endpoints
- `IsInstructorUser` — `user_type == instructor`
- `IsVerifiedInstructor` — instructor with approved `IdentityVerification`
- `IsVerifiedPartnerInstitution` — partner institution with `is_verified=True` and `is_active=True` on their profile
- `IsVerifiedCourseCreator` — passes for either `IsVerifiedInstructor` OR `IsVerifiedPartnerInstitution`; used on all course authoring endpoints
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

### Certificate System

`Certificate` is auto-issued when `recalculate_progress()` transitions an enrollment to 100% for the first time. The issuance callback fires via `transaction.on_commit(lambda: _issue_certificate_and_notify(enrollment.pk))` in `enrollment_service.py`. `issue_certificate()` uses `get_or_create` — idempotent under Celery redelivery. PDF rendered on-the-fly by reportlab (`courses/certificate_pdf.py`) — no file stored on disk. Public UUID-based URLs (`certificate_uid`) are non-guessable. See `docs/architecture/14-certificate-system.md`.

### Review & Rating System

`CourseReview` — one review per enrolled learner per course, enforced by `OneToOneField(enrollment)` (primary) and `UniqueConstraint(user, course)` (belt-and-braces). `ReviewVote` — one vote per `(review, voter)` pair; row is **mutated** (flag flipped) rather than deleted+recreated on direction change.

**Denormalized fields on `NidusCourse`:** `avg_rating` (DECIMAL 3,2) and `review_count` (INT). Updated after every review create/edit/delete via `_recalculate_course_avg(course_id)` called through `transaction.on_commit`. Never updated directly — always through the service. Enables O(1) catalog sort (`?sort=rating`) and filter (`?rating_min=`, `?min_reviews=`) without subqueries.

**Atomic vote flip:** `vote_on_review()` holds `select_for_update()` on the `ReviewVote` row, then updates both counter fields in a single `UPDATE` via `F()` expressions — no read-modify-write race.

**`ReviewError(message, http_status)`** — same pattern as `AssignmentSubmissionError` and `InviteError`. Views use `exc.http_status` directly.

**Viewer-vote annotation:** `CourseReviewListView.get` annotates the queryset with a `Subquery` for the requesting user's vote (`_viewer_vote`). One extra DB query per page, not per row. Unauthenticated requests skip the annotation.

**Service:** `courses/services/review_service.py`. **Views:** `courses/all_views/review_views.py`. **Serializers:** `courses/all_serializers/review_serializers.py`. **Models:** `courses/all_models/review_models.py`. See `docs/architecture/15-review-rating-system.md`.

**Access-denied policy (follows project-wide slug/ID rule):**
- Slug-based (`<slug>/reviews/*`) → 404 when course not found/not published
- Numeric ID (`reviews/<review_id>/vote/`) → 404 on no-access (ID not public-enumerable)
- Self-vote → 422

### Course Status State Machine

`NidusCourse.transition_to(new_status, reviewer=None, rejection_reason='')` in `courses/models.py` is the single entry point for all status changes. Valid transitions:

| From | To | Who |
|------|----|-----|
| `draft` | `under_review` | **Individual** instructor (via `/submit/`) — direct to admin |
| `draft` | `institution_review` | Expert on an **institution-owned** course (via `/finish/`) |
| `institution_review` | `under_review` | Owning partner institution (via `/institution-review/` `action: submit`) |
| `institution_review` | `rejected` | Owning partner institution (via `/institution-review/` `action: send_back` + `rejection_reason`) |
| `under_review` | `published` | Admin (via `/review/` with `action: approve`) |
| `under_review` | `rejected` | Admin (via `/review/` with `action: reject`) |
| `rejected` | `draft` | Instructor/expert (via `/rework/`) |
| `published` | `archived` | Instructor or Admin (via `/archive/`) |
| `archived` | `draft` | Instructor or Admin (via `/archive/` → rework) |

**Two-stage submission for institution-owned courses** (`partner_institution` set): the expert calls `/finish/` (→ `institution_review`, content then frozen), and the institution forwards to the admin (`/institution-review/` `submit`) or returns it to the expert (`send_back`). Individual-instructor courses (no `partner_institution`) keep the direct `/submit/` → `under_review` path. `transition_to()` enforces this by ownership: leaving `draft`, an institution course may only go to `institution_review` and an individual course only to `under_review` (cross-routing → `ValidationError`). Expert `/finish/` is scoped to `instructors` (institution user → 404); `/institution-review/` is `IsVerifiedPartnerInstitution`-gated and scoped to the owning institution. See `docs/future_implementations/INSTITUTION_COURSE_SUBMISSION_FLOW.md`.

Leaving `draft` (to either `under_review` or `institution_review`) runs `_validate_course_completeness()`: checks title/description, at least one section, each section has content, all videos `status=ready`, all quizzes have questions with correct answers. `institution_review` is **not** editable (content frozen, like `under_review`).

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
- Protected endpoints authenticate from **either** the `Authorization: Bearer` header **or** the `access_token` cookie. `CookieJWTAuthentication` (`authentication/authentication.py`) is registered first in `DEFAULT_AUTHENTICATION_CLASSES`, then header-based `JWTAuthentication`. Cookie name is `JWT_ACCESS_COOKIE_NAME` (default `access_token`) — keep the auth class and `cookie_helpers.py` reading the same setting.
- Token refresh: `POST /api/v1/auth/token/refresh/`
- OAuth: authorization-code flow for Google and LinkedIn; callback URLs configured via env vars

### Transactional Email (async via Celery)

All auth emails go through Celery tasks in `authentication/tasks.py` — views enqueue and return immediately; the worker does the SMTP send + retries. `send_otp_email` / `send_credentials_email` in `authentication/utils/email_utils.py` are the sync senders, now called **only** from inside those tasks.

- `send_otp_email_task(user_pk, otp_code, purpose)` — registration / resend / password-reset OTP. Enqueued from `auth_views`, `otp_views`, `password_views`. Tight retry profile (`max_retries=2`, `retry_backoff_max=30`) because **OTP expires in 2 min** — a late retry is useless; resend is the real recovery. `otp_code` is captured at enqueue time so a later regeneration doesn't change what a queued task delivers.
- `send_expert_credentials_email_task(user_pk, password, institution_name)` — institution-onboarded expert credentials (see *Partner Institution: Experts*).
- Views surface **503 only on broker-enqueue failure** (Redis down), not on SMTP failure — the actual send is async, so an SMTP failure is retried by the worker and otherwise recovered via resend. `password/forgot/` stays generic regardless (no user enumeration). **Without a running worker, emails are never sent and no 503 is raised** — monitor queue depth in prod.

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

Detailed design rationale is in `docs/architecture/` (18 numbered design docs, `01`–`18`, plus a `README.md` guide map). `01-system-overview.md` is the at-a-glance tour (apps, layers, request lifecycle) and is worth reading before making structural changes. `09-coding-exercises.md` covers the coding exercise data model, authoring API, and design decisions. `14-certificate-system.md` covers the completion certificate issuance flow, PDF generation, and public share URLs. `15-review-rating-system.md` covers the review/rating data model, vote atomicity, denormalized catalog fields, and access-denied policy. `16-notification-system.md` covers the notification dispatcher, event types, and WebSocket delivery. `17-messaging-system.md` covers the messaging data model, REST + WebSocket protocol, unread semantics, and frontend client contract (see also `docs/api-testing/postman-messaging.md`). `18-partner-institutions.md` covers institution verification, expert onboarding, departments, and course creation + roster assignment; `docs/api-testing/postman-partner-institution.md` is its manual-test guide. `FRONTEND_ERROR_RESPONSE_FORMAT.md` defines the error shape all views must follow.
