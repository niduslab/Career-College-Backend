# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Keep this file current.** Whenever you rename a symbol, add a new pattern, change a convention, or introduce an architectural rule, update the relevant section of this file in the same change. Stale guidance is worse than no guidance. 

> **Follow these instructions:** 1. Follow the existing code structure while creating new feature/app. 2. Always update the related docs or create new docs if necessary after building or updating a feature. 3. Create the docs in easy language so everybody can understand. 4. Keep the comments/docstrings very clen and concise, do not overstatement on a comment. 5. Add the reusable features in **/core** directory if they can be used later by other apps.

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
| `webinars` | `/api/v1/webinars/` | Institution-owned live webinars (external meeting link), publish state machine, catalog + learner registration |
| `analytics` | `/api/v1/analytics/` | Read-only institution analytics dashboard (KPI summary + trends + top-courses); aggregates over other apps, owns no models |
| `payments` | `/api/v1/payments/` | SSLCommerz hosted-checkout payments for courses **and** webinars: `Order` lifecycle, gateway callbacks, learner order history; produces PAID enrollments / webinar registrations |
| `admin_console` | `/api/v1/admin-console/` | Platform back-office. First slice: **session-based admin login** (csrf/login/logout/session). Model-less so far; base view for all future admin endpoints. |
| `core` | — | Shared permissions, pagination, middleware |

### Messaging

`messaging/` — a `Conversation` is a **role-neutral 2-party thread** selected by `conversation_type`; the two parties live in `ConversationParticipant` (each row carries that user's `last_read_at` cursor). Three types, each with its own send-gate, all dispatched by `conversation_type` in `messaging/services/messaging_service.py` → `_assert_send_permission()` (send-time) and `_validate_new_conversation()` (create-time):

| `conversation_type` | parties | course | send-gate |
|---|---|---|---|
| `learner_instructor` | learner ↔ instructor (learner-initiated) | required | learner active enrollment / instructor still on course |
| `co_instructor` | instructor ↔ instructor | required | each must still be in `course.instructors` |
| `institution_expert` | partner-institution user ↔ affiliated expert (institution-initiated) | optional (nullable) | expert must be an active affiliate; institution party always may |

The gate is enforced **only** in the service — never duplicate it in a view or consumer. **Follow-up messages have no REST endpoint**: only the conversation opener is persisted via `POST conversations/create/`; every reply is sent over the WebSocket `messaging` stream (`send_message`), where the gate runs and returns an `error` frame on violation. The REST surface is create / list / detail / read / unread-count only. Roles within a thread are derived from `user.user_type` + `conversation_type`, not a stored role. Pair uniqueness is `(conversation_type, course, participant_key)` where `participant_key` is `"<minid>-<maxid>"` (two partial unique constraints handle the nullable-course case). `start_conversation(*, conversation_type, initiator, target, course, opener_body)` is the create entry point; `get_or_create_conversation(learner, instructor, course, opener_body)` is a back-compat shim for the learner↔instructor path. After a message commits, `_push_ws_and_notify` pushes a `new_message` channel event **only to the recipient's** `messaging_user_{id}` group (the sender already has it via the `message_sent` ack or 201 body — pushing to both causes duplicate delivery) and dispatches `MESSAGE_RECEIVED` (course context is optional — the builder tolerates a null course). Unread: `get_unread_counts()` (per-conversation counts, WS on-connect) and `get_unread_conversation_count()` (threads with ≥1 unread) both read the caller's participant cursor and always agree. REST/WS endpoints allow learner/instructor/partner_institution user types (`_MESSAGING_USERS`, admins excluded). Numeric IDs → 404 on no-access. Announcements (institution → many learners) are **not** modeled here — that's notification fan-out (see `docs/future_implementations/INSTITUTION_MESSAGING.md` §8, unbuilt). See `docs/architecture/17-messaging-system.md`.

### Realtime / WebSocket

`realtime/` — a single ASGI `PlatformConsumer` at `/ws/` multiplexes per-feature streams via `{"stream": "<name>", "payload": {...}}`. JWT is passed as a `?token=` query param and validated on connect (close `4001` on failure). Cross-process delivery uses the Redis channel layer (`group_send`). Add a stream by registering a handler class in `realtime/streams/` and wiring it into `_STREAM_HANDLER_CLASSES` / `_CHANNEL_EVENT_DISPATCH` in `realtime/consumers.py`. Stream handlers run async — wrap ORM calls in `database_sync_to_async`.

### Custom User Model

`authentication/models.py` — email-based (no username field), with `user_type` field: `learner`, `instructor`, `partner_institution`, `admin`. On creation, a signal (`authentication/signals.py`) auto-creates the matching profile (`LearnerProfile`, `InstructorProfile`, or `PartnerInstitutionProfile`). `AUTH_USER_MODEL = 'authentication.User'`.

### Course Content Ordering: SectionContent

The `SectionContent` model (in `courses/`) is the **single source of truth for ordering** within a section. It holds a `GenericForeignKey` that points to a `Lecture`, `Quiz`, or `CodingExercise`. When adding new content types, create the model and then create a `SectionContent` row linking it into the section — do not add ordering directly to content models. Each content model must have a `GenericRelation` to `SectionContent` so that deleting the object cascades and removes its curriculum slot automatically. Reordering logic lives in `courses/services/section_service.py` → `reorder_section_content()`.

### Content Authorship: AuthoredModel

`AuthoredModel` (abstract, in `courses/all_models/course_models.py`, extends `TimestampedModel`) adds `created_by` + `last_edited_by` (both FK→User, `SET_NULL`, nullable, `related_name='+'`) to every content model an expert/instructor authors: `CourseSection`, `SectionContent`, `Lecture`, `Quiz`, `Assignment`, `CodingExercise`. Powers partner-institution monitoring — which expert authored / last touched a content row (SRS 7.2.1, 7.7.3). **New authored content models must inherit `AuthoredModel`, not `TimestampedModel`.**

Stamping is centralised in `courses/utils.py` → `save_authored(serializer, user, **extra)`: sets `created_by` only on create (no bound instance), `last_edited_by` on every save, and passes extra kwargs through to `serializer.save()`. Every content create/update view path uses it. Non-serializer create paths (`Assignment`/`CodingExercise` `objects.create`, `create_section_content_for_object(..., created_by=user)`, `assignment_service.update_assignment`) set the fields directly. `QuizQuestion` and `QuizAnswer` are **not** `AuthoredModel` (sub-rows of an already-authored parent). Never trust a client-supplied author — always stamp from `request.user`.

The read serializers (`CourseSectionSerializer`, `LectureSerializer`, `SectionContentSerializer`, `QuizSerializer`, `CodingExerciseSerializer`, `AssignmentSerializer`) expose `created_by` + `last_edited_by` as nested `InstructorBriefSerializer` (`id`, `full_name`, `email`), read-only. List loaders `select_related('created_by', 'last_edited_by')` (`get_course_sections`, `get_section_lectures`, and the `SectionContent` list query) so the author fields don't N+1.

### Course Detail Endpoints

Four distinct course detail surfaces, split by audience and concern. Do not collapse them into one conditional endpoint:

| Endpoint | Audience | Purpose |
|---|---|---|
| `GET /catalog/<slug>/` (`CatalogCourseDetailView`, `AllowAny`) | Guests / unenrolled | Marketing page — course metadata + full curriculum outline (titles, durations) + preview lecture HLS URLs only for `Lecture.is_preview=True`. Stays as a one-shot tree because catalog browsing is a guest workflow and SEO benefits from a single page render. |
| `GET /my-courses/<slug>/` (`MyCoursesDetailView`, `IsEmailVerified`) | Enrolled learner OR course's own instructor | **Slim metadata only**: title, description, instructors, learning objectives/prerequisites/audiences (plain newline-separated `TextField`s on `NidusCourse` — not sub-resources), totals, plus the caller's enrollment block (progress %, completed_at, last_accessed_at) and `is_instructor` flag. Curriculum tree and per-item content **do not** live here — fetch them from `/learn/<slug>/curriculum/` and `/learn/<thing>/<id>/`. |
| `GET /learn/<slug>/curriculum/` (`LearnerCurriculumView`) | Same as above | Sidebar curriculum outline (sections + items, lightweight). See *Learner Consumption Endpoints* below. |
| `GET /<int:pk>/` (`CourseDetailView`, `IsCourseCreator`) | Course's own instructor OR partner institution owner | Authoring/edit surface (GET + PATCH metadata). Curriculum edits flow through `/sections/`, `/lectures/`, `/contents/`. Identity verification **not** required — see `IsCourseCreator` in *Permissions*. |

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

Auto-grading lives in `courses/services/assignment_grading.py`. `RubricGrader().grade(answer_text, rubric_snapshot, max_score) → (score, criterion_results, feedback)` runs deterministic per-criterion matchers (`keyword`, `regex`, `min_length`, `max_length`, `any_of`, `all_of`). Adding a new criterion type is additive: register a matcher in `_MATCHERS` (grader) and a value-validator in `_RUBRIC_CRITERION_VALUE_VALIDATORS` (authoring serializer). The grader is defensive — an unknown type or a matcher that raises is recorded as a miss, never crashes the grading task. An **empty rubric grades to 0** (short-circuit on `not rubric_snapshot`) — deterministic, no fallback inside the grader.

Because an empty rubric silently zeroes every answer, authoring **auto-generates a rubric from the model answer** (`courses/services/rubric_autogen.py` → `generate_rubric_from_model_answer(model_answer, points, max_terms=5, split_points=True)`, pure/no-Django). It extracts the top-N significant words (stopwords + <3-char words dropped, frequency-ranked, alphabetical tiebreak for determinism) and splits them into several `all_of` groups, dividing `points` across the groups (group count = `min(points, keyword_count)`; sum lands exactly on `question.points`). Two entry points: (1) `_autofill_rubric(question)` in `assignment_service.py` runs on `add_question`/`update_question` **before save** — fires only when `model_answer` is non-empty **and** `rubric` is empty (`split_points=True`, never overwrites an authored rubric), so a skipped rubric still grades; (2) `POST /api/v1/courses/assignments/rubric-preview/` (`AssignmentRubricPreviewAPIView`, `IsInstructorUser`, body `{model_answer, points, max_terms?}`) returns the same groups **without saving** and with `points: 0` (`split_points=False`) for the authoring UI, where the instructor assigns points manually. Blank/stopword-only answer or `points <= 0` → empty rubric (grading stays 0). `AssignmentQuestion.points` defaults to **0** (no implicit weight). `total_score` and the sum of question points stay independent server-side; `AssignmentSerializer.get_max_score` exposes the sum separately (the authoring UI enforces sum == total_score client-side, not the backend).

Grading runs out-of-band via `grade_assignment_submission_task` in `courses/tasks.py`, mirroring the video transcoding pattern. The task is decorated `acks_late=True` so a worker death mid-task causes the broker to redeliver; the next invocation either resumes or short-circuits because the status is already terminal. On final retry exhaustion the task marks the submission `grading_failed` with a truncated error message — the learner can re-enqueue via the `/retry/` endpoint.

Coding-exercise learner runtime is **built** (Docker-backed). **Grading is script evaluation (Udemy-style)** and exercises are **single-language**: `CodingExercise` carries `language`, `starter_code`, `solution_code`, and `evaluation_script` directly — a test file (Python `unittest` etc.) that imports/calls the learner's code and asserts on it. There are **no I/O test-case pairs** (`CodingTestCase` removed in `0023_script_only_evaluation`) and **no per-language config model** (`CodingExerciseLanguageConfig`, `problem_statement`, `difficulty`, `default_language`, `supported_languages` all removed in `0024_single_language_coding_exercise` — `description` is the problem text). Each test in the script yields one named `CodingSubmissionTestResult` row (`test_name`, status, stdout, stderr/traceback). Run and Submit both execute the full suite (no hidden/visible split). `CodingSubmission.total_tests` is 0 while queued/grading — the script decides the count, the grading task back-fills it. Endpoints (`courses/urls.py`), all on numeric IDs → 404 on no-access:

| Endpoint | View | Purpose |
|---|---|---|
| `GET /learn/coding-exercises/<id>/` | `LearnerCodingExerciseDetailView` | Exercise detail — problem + starter code per language, caller's latest-submission summary. Never `solution_code` / `evaluation_script`. |
| `POST /learn/coding-exercises/<id>/run/` | `LearnerCodingRunView` | Transient run of the evaluation script. Dispatches `evaluate_coding_run_task`; nothing persisted. Returns a `task_id` to poll. `422` if the language has no evaluation script. |
| `POST /learn/coding-exercises/<id>/submit/` | `LearnerCodingSubmitView` | Persisted submission. Creates `CodingSubmission`, dispatches `evaluate_coding_submission_task`. `422` if the language has no evaluation script. |
| `GET /learn/coding-exercises/tasks/<task_id>/` | `LearnerCodingTaskStatusView` | Poll the Celery `AsyncResult` for a run/submit. |
| `GET /learn/coding-exercises/submissions/<id>/` | `LearnerCodingSubmissionDetailView` | Learner sees own only. Per-test results with `test_name` + failure message. |
| `POST /learn/coding-exercises/submissions/<id>/retry/` | `LearnerCodingSubmissionRetryView` | Re-enqueue a submission stuck in `error`. Other statuses → `422`. |

Loaders in `courses/services/learner_service.py`: `get_coding_exercise_for_consumption`, `run_coding_exercise`, `submit_coding_exercise`, `get_learner_coding_submission`, `retry_coding_submission`; `_validate_language` rejects a language ≠ `exercise.language` (400), `_validate_evaluation_script` gates run+submit (422 when the script is blank). `CodingSubmissionError(http_status=...)` mirrors `AssignmentSubmissionError`. Execution: `CodeRunner.run_submission(code, evaluation_script, time_limit_ms, language)` (Docker, one container per submission, zero-dependency per-language micro-harnesses — Python `unittest`, Node `assert` + `test()` registry, Java reflection over `test*`, C++ `TEST()` macro header) in `courses/services/code_runner.py`, returning `list[ScriptTestResult]`; tasks `evaluate_coding_run_task` / `evaluate_coding_submission_task` in `courses/tasks.py`; `reap_stuck_coding_submissions_task` (Celery beat) recovers stuck rows. `time_limit_ms` is the **whole-suite** budget. `CodingSubmission.status`: `queued → grading → passed | failed | error`. `_validate_course_completeness` blocks leaving `draft` while any coding exercise has a blank `evaluation_script`. Learner serializer `LearnerCodingExerciseDetailSerializer` (in `courses/all_serializers/learner_serializers.py`) never declares `solution_code` or `evaluation_script`. See `docs/architecture/09-coding-exercises.md`.

`recalculate_progress()` in `enrollment_service.py` counts lecture completion (`WatchProgress.is_completed=True`), quiz attempts (≥1 `QuizAttempt` per quiz), and assignment submissions in the `passed` state (`AssignmentSubmission.status='passed'`). Lectures recalc via the `WatchProgress` post_save signal; `submit_quiz_attempt` calls `recalculate_progress(enrollment)` directly at the end of its transaction; `grade_assignment_submission_task` schedules `recalculate_progress` via `transaction.on_commit` only when the final status is `passed`. Coding-exercise completion **counts** — `recalculate_progress` (`enrollment_service.py:394-409`) tallies distinct exercises with a `CodingSubmission.status='passed'`; `evaluate_coding_submission_task` triggers the recalc on pass.

**`Enrollment.completed_at` is sticky — never clear it.** `recalculate_progress` used to reset it whenever progress fell back below 100. Because `total_items` counts every `SectionContent` row, an instructor adding one lecture silently un-completed every learner who had already finished (the course vanished from My Courses' "Completed" tab and from the dashboard's `courses_completed` on their next watch tick), while `issue_certificate` — `get_or_create`, never revoked — left the certificate in place. The two records contradicted each other. Completion now records "the learner finished the course as it existed then"; `progress_percent` still moves, so later additions show as an unfinished remainder. Regression: `test_adding_content_after_completion_does_not_uncomplete`.

**`GET /my-courses/` takes `?status=all|in_progress|completed`** (validated in `get_learner_enrollments`; unknown → 400) and returns a **`status_counts`** block beside the paginator keys, from `get_learner_enrollment_status_counts` (one aggregate). The counts must stay server-side — they describe the whole enrollment set, and the frontend previously counted rows in a single unpaginated page, which capped My Courses at 10 enrollments and made a finished course the first thing to disappear (the list orders by `last_accessed_at DESC NULLS LAST`). Frontend callers needing every enrollment rather than a page pass `page_size: ALL_ENROLLMENTS_PAGE_SIZE`.

**A completed course the learner later unenrolled from must stay in My Courses.** `unenroll_learner` is a soft revoke — it flips `is_active` but preserves `completed_at` and never revokes the certificate — so filtering the list on `is_active=True` hid a genuinely finished course, stranded its certificate, and made My Courses report 0 completed while the summary reported 1. `_learner_enrollment_scope` widens the filter to `Q(is_active=True) | Q(completed_at__isnull=False)` behind the **opt-in** `include_unenrolled_completed` flag that `MyCoursesListView` passes. Opt-in and not the default because `get_continue_target` shares the queryset and must never resume a course the learner has lost access to. Only unenrolled *and unfinished* rows stay hidden: `in_progress` (filter and count) pins `is_active=True`, so an unenrolled completed course shows under **Completed** only. Regressions: `test_completed_then_unenrolled_course_still_appears`, `test_unenrolled_course_is_never_the_resume_target`.

`upsert_watch_progress` enforces two server-side invariants the client cannot override: `watched_seconds` is clamped to the active `VideoAsset.duration_seconds` (HLS players legitimately overshoot by a fraction, so we cap rather than reject), and if the clamped cursor lands at duration, `is_completed` is forced to `True` (the video has functionally ended regardless of what the client declared). Article lectures have no duration; `watched_seconds` is forced to `0`.

### Learner-Safe Serialization

The following fields **must remain instructor-only** in any learner-facing response:

| Field | Audience | Status |
|---|---|---|
| `Lecture.stream_master_playlist` (full HLS URL) | Exposed on `/learn/lectures/<id>/` for any caller with access; on `/catalog/` only when `Lecture.is_preview=True` | Done |
| `QuizAnswer.is_correct` | Instructor only (omit from learner payload pre-submit; score server-side; reveal the correct answer in the post-submit response only for wrong questions) | Done — `_LearnerQuizAnswerOptionSerializer` simply doesn't declare it; `build_quiz_attempt_result` controls reveal-on-wrong |
| `AssignmentQuestion.model_answer` | Instructor only (omit from learner attempt payload; reveal in submission detail only when `status in (passed, failed)`) | Done — `_LearnerAssignmentQuestionSerializer` doesn't declare it; `build_assignment_submission_result` controls reveal-on-graded; authoring `AssignmentQuestionSerializer.to_representation` strips it for non-instructors |
| `AssignmentQuestion.rubric` | Instructor only (grading-rule definition; never exposed to learners pre- or post-submit, including the snapshotted copy on submission rows) | Done — `_LearnerAssignmentQuestionSerializer` doesn't declare it; authoring serializer strips it for non-instructors; `rubric_snapshot` on `AssignmentSubmissionAnswer` is consumed by the grader, never serialized to learners |
| `CodingExercise.solution_code` | Instructor only | Done — `LearnerCodingExerciseDetailSerializer` declares only starter code, never `solution_code` |
| `CodingExercise.evaluation_script` | Instructor only — it IS the grading key; leaking it hands the learner every expected answer | Done — `LearnerCodingExerciseDetailSerializer` never declares it; only the instructor-gated `CodingExerciseSerializer` carries it |

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

**Expert onboarding (auto-provision).** A verified institution onboards experts via `authentication/services/expert_service.py` → `provision_expert()`: creates a `User(user_type='instructor')` (the profile signal fires), then sets `InstructorProfile.affiliated_institution`, `onboarding_source='institution'`, `affiliation_status='active'`, `affiliated_at`, **`is_verified=True`** (the institution vouches — institution-onboarded experts skip their own `IdentityVerification` and can author immediately) and **`is_email_verified=True`** on the `User` (no OTP email-ownership proof needed). The account is created with a **preset password** (`secrets.token_urlsafe(9)`), emailed to the expert (login email + password) via `send_expert_credentials_email_task` enqueued on `transaction.on_commit` — the expert can log in immediately, no OTP step. The plaintext password is passed as a task arg and is deliberately kept out of the `EXPERT_ONBOARDED` notification payload (`skip_email=True`) so it is never persisted in `Notification.data`. Deactivation (`set_expert_active(..., active=False)`) flips `affiliation_status='removed'` **and** `is_verified=False`. Endpoints `/api/v1/auth/partner/experts/` (list/create) and `/<id>/` (detail/patch), gated `IsVerifiedPartnerInstitution`; every query scoped to the caller's own institution; numeric-ID detail → 404 on no-access.

**Known gap (unresolved):** deactivation does **not** remove the expert from `course.instructors`, and course/section/lecture/quiz/assignment/coding-exercise authoring endpoints now gate on `IsCourseCreator`/`IsInstructorUser` (verification not required — see *Permissions*), not `IsVerifiedInstructor`. So a removed expert currently **keeps** authoring access to any course they were rostered on — the `is_verified=False` flip no longer revokes anything at the content layer. Fixing this needs an active-affiliation check independent of `is_verified` (which now also covers "not yet verified new instructor," a state that must stay authoring-capable). Do not assume "removed expert can't author" holds until this is fixed. (Partial mitigation: `/finish/` and `/submit/` — `CourseMarkFinishedView`/`CourseSubmitForReviewView` in `status_views.py` — are still gated `IsVerifiedCourseCreator`, so a removed expert can still mutate draft content but cannot push it to `institution_review`/`under_review`.)

**Departments.** An institution defines its own departments (`Department` model in `authentication/models.py`, owned by `PartnerInstitutionProfile` via `institution` FK, name unique per institution case-insensitively). `InstructorProfile.department` is a nullable FK to `Department` (`on_delete=SET_NULL`, rename-safe). CRUD lives in `authentication/services/department_service.py`; endpoints `/api/v1/auth/partner/departments/` (list/create) and `/<id>/` (detail/patch/delete), gated `IsVerifiedPartnerInstitution`, scoped to the caller's institution, numeric-ID → 404. **DELETE soft-deactivates** (`is_active=False`) — assigned experts keep their FK; inactive departments are excluded from the default list. An expert's `department_id` (on `provision_expert`/`update_expert`) is validated by `resolve_expert_department()` to be an **active department of the expert's own institution** — a foreign/unknown id raises `ExpertError(422)` (mirrors the roster cross-institution rule).

**Course creation.** Partner institutions create courses through the same `CourseCreateAPIView` as instructors (`IsCourseCreator` — identity verification not required to create/author; see *Permissions*). `NidusCourseCreateUpdateSerializer.create()` already sets `partner_institution` and skips `instructors.set([self])` for partner creators. `NidusCourse.clean()` permits `created_by.user_type in ('instructor', 'partner_institution')` — **never narrow this back to instructor-only.**

**Roster assignment (direct add, no invite/accept).** Partner institutions own the roster and add experts directly via `courses/services/institution_course_service.py` → `add_course_instructor()` / `remove_course_instructor()`, exposed at `POST/DELETE /api/v1/courses/<pk>/institution-instructors/[<expert_user_id>/]` (`InstitutionCourseInstructorView`, `IsVerifiedPartnerInstitution`). Only an **active affiliated expert** of the owning institution may be added, and only while the course `is_editable()`. This is distinct from the instructor `CourseInstructorInvite` flow (which requires an accept step) — do not conflate them. Assigned experts edit content through the existing `CourseDetailView` / content endpoints (`Q(instructors=user) | Q(created_by=user)` already covers them). `InstitutionCourseError(message, http_status)` mirrors `InviteError`.

### Scheduled Courses (Cohorts)

Cohort delivery layered on the existing course — see `docs/architecture/22-scheduled-courses.md`, `docs/architecture/23-scheduled-course-lifecycle.md`, and `docs/api-testing/postman-schedules.md`. `CourseSchedule` (`courses/all_models/schedule_models.py`, inherits `AuthoredModel`) wraps a `NidusCourse` (`course.schedules`, many per course — re-runs are new schedules, never duplicated courses): `cohort_label`, `timezone` (unvalidated, mirrors `Webinar.timezone`), `enrollment_opens_at`/`enrollment_closes_at`, `start_date`, `end_date` (nullable = open-ended), `max_seats` (nullable = unlimited), `status`. A course with no schedules is plain self-paced — untouched.

**`delivery_mode`** (`NidusCourse.DeliveryMode`: `self_paced` | `scheduled`) is set at creation and **immutable afterward** (`validate_delivery_mode()` on the update serializer rejects any change). It changes what `_validate_course_completeness()` demands of the curriculum:

| | `self_paced` | `scheduled` |
|---|---|---|
| Sections | required (≥1), and every section must have content | **not required at all** — a scheduled course may submit with zero sections |
| `NidusCourse.course_outline` (plain `TextField`, blank by default) | not required | **required** — must be non-blank before submission; stands in for a fully-authored curriculum so the admin can judge scope |
| Attached `CourseSchedule` | n/a | required (≥1), with structurally sane dates (`date_logic_errors()`) |

Sections/content are still fully supported for scheduled courses (drip content added later, see below) — they're just optional at submission time. `course_outline` is exposed read-only on `NidusCourseSerializer`, `CatalogCourseDetailSerializer`, and the my-courses meta serializer, and writable on `NidusCourseCreateUpdateSerializer` (normalized like `learning_objectives`/`prerequisites`/`audiences`).

**State machine** (`transition_to(new_status, actor=None)`, single entry point — never set `status` directly): `draft → scheduled → ongoing → completed → archived → draft`, plus `scheduled → draft` (rework valve for premature activation). `draft → scheduled` runs `_validate_activation()`: course `published`, `opens < closes <= start`, `end > start` if set, close/start in future (dict `ValidationError` → 400 with `errors`; illegal transition → plain string → 422). `scheduled → ongoing` and `ongoing → completed` flip **automatically** via the beat task `advance_course_schedules_task` (every 5 min, per-row `transition_to` in per-row try/except — **no info logs in schedule code; error/exception only**). `is_editable()` = `draft|scheduled` (dates PATCHable until start, frozen once `ongoing`); DELETE draft-only → 422 otherwise.

**Ownership** (`courses/services/schedule_service.py`, `ScheduleError(message, http_status)` mirrors `WebinarError`): institution-owned course → schedule mutations institution-only; individual course → creator-only; course roster experts get **read-only** visibility (`get_course_for_schedule_read`). Endpoints (`IsVerifiedCourseCreator`-gated, object-level in service, numeric-ID → 404 "Course not found." — no existence leak): `GET/POST /<pk>/schedules/`, `GET/PATCH/DELETE /<pk>/schedules/<id>/`, `POST .../activate|archive|rework/`. **Invariant:** a cohort may only be attached to a `delivery_mode=scheduled` course — `assert_course_supports_schedules(course)` gates `POST /schedules/` (self-paced course → `ScheduleError(422)`). Nothing downstream (checkout, enroll) re-checks `delivery_mode`, so this create-time guard is the single enforcement point.

**Cohort enrollment:** `POST /<slug>/enroll/` takes optional `{"schedule_id": N}` (omit = self-paced, byte-identical to before). `enroll_learner(..., schedule=None)` enforces: status `scheduled`, window `opens <= now <= closes` (422), capacity via `select_for_update()` **on the schedule row before counting** (webinar race fix; full → 422). Paid-course gate runs before schedule logic; `Order` knows nothing about schedules. `Enrollment.schedule` (nullable FK) replaced the old `(user, course)` unique with two partial uniques: `(user, course) WHERE schedule IS NULL` and `(user, schedule) WHERE schedule IS NOT NULL` — a learner may retake a later cohort, and may hold self-paced + cohort rows simultaneously (access resolution prefers the self-paced row: `resolve_course_access` orders `schedule_id` nulls-first).

**Drip release:** `CourseSection.unlocks_at` (nullable; NULL = released) gates learners section-level. `guard_editable(course, section=None)` (`courses/utils.py`) has a carve-out: a `published` course with a **scheduled or ongoing** schedule stays content-**creatable** at any point — instructors can author ahead of `start_date`, not just once the cohort is live; closes again once every schedule reaches `completed`/`archived`. Within that carve-out, editing/deleting something that **already exists** additionally requires passing `section=`: if that section is already released to learners (`unlocks_at` null or in the past), the request is blocked (422 "This content has already been released to learners and cannot be edited.") even though the course is otherwise open for new content. Only genuine creates omit `section=`; every PATCH/PUT/DELETE on an existing section/lecture/quiz/question/answer/assignment/coding-exercise passes its owning section so the lock applies. No admin re-review for drip additions — `AuthoredModel` stamping is the audit trail. Section create/update serializers accept `unlocks_at`.

**Learner gates** — one function, `assert_content_released(enrollment, section)` (`learner_service.py`), raising `ContentNotReleasedError` (422 — timing rule, never 403/404): (1) cohort enrollment before `schedule.start_date` → "This course has not started yet."; (2) future `section.unlocks_at` → "This content has not been released yet." (applies to **all** learners incl. self-paced). Wired inside the four consumption loaders (lecture/quiz/assignment/coding) and called explicitly in the inline-fetch write views (progress, quiz submit, assignment submit) — locked content blocks reads **and** writes. `load_learner_curriculum` never blocks: locked sections stay listed with `is_locked: true` + `unlocks_at` (pre-start → all sections locked). Instructor preview bypasses every gate (`enrollment is None` → no-op). After `end_date`: **lifetime access, nothing revoked** — status flip is bookkeeping only; submissions stay open.

### Analytics Dashboard (`analytics/`, `/api/v1/analytics/`)

Read-only institution analytics over existing data — its **own app**, owns **no models** (pure aggregation over `courses`/`webinars`/`authentication`). Deliberately separate from the `authentication` partner-console endpoints (experts/departments) because the data is course/webinar-shaped, not auth-shaped. All queries are scoped to `request.user.partner_institution_profile` — never a client-supplied institution id. Aggregation lives in `analytics/services/analytics_service.py`; views in `analytics/all_views/analytics_views.py` (no lazy-import dance needed — `analytics` depends on `courses`/`webinars` one-directionally, no cycle). All endpoints gated `IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution`.

| Endpoint | View | Purpose |
|---|---|---|
| `GET /api/v1/analytics/partner/summary/` | `InstitutionAnalyticsSummaryView` | One-shot KPI cards: courses (total/published/draft + status breakdown + weighted avg rating), enrollments (active/all-time/growth/active-learners/completion-rate/avg-progress), certificates, webinars (status + upcoming/live/completed + registrations), roster, engagement composite. |
| `GET /api/v1/analytics/partner/enrollments/trend/` | `InstitutionEnrollmentTrendView` | Enrollment time series. `?granularity=monthly\|weekly&periods=N`. |
| `GET /api/v1/analytics/partner/webinars/trend/` | `InstitutionWebinarTrendView` | Webinar-registration time series. |
| `GET /api/v1/analytics/partner/certificates/trend/` | `InstitutionCertificateTrendView` | Certificate-issuance time series. |
| `GET /api/v1/analytics/partner/top-courses/` | `InstitutionTopCoursesView` | Ranked courses. `?sort=enrollments\|rating\|completion&limit=N`. |
| `GET /api/v1/analytics/partner/experts/performance/` | `InstitutionExpertPerformanceView` | Per-expert outcome metrics for the whole active roster (courses credited, content authored, avg rating, enrollments, completion, certificates, webinars hosted, last-active). |
| `GET /api/v1/analytics/partner/experts/<expert_id>/performance/` | `InstitutionExpertPerformanceDetailView` | One expert (numeric id → 404 if not this institution's active affiliate). |

**Expert performance** (`analytics/services/expert_performance_service.py` → `expert_performance(institution, *, expert_id=None)`): drills below the institution-wide dashboard to per-expert outcomes. **Attribution:** a course is credited to every user in `course.instructors` **and** its `created_by` (co-taught courses count toward each — per-expert sums can exceed the institution total; surfaced as `attribution` in the payload). Content-authorship counts use `created_by` on `CourseSection`/`Lecture`/`Quiz`/`Assignment`/`CodingExercise` (exact per expert). Cost is a fixed ~12 grouped queries independent of roster size — per-course aggregates computed once, summed per expert in Python. Every active affiliate is listed (zero-activity experts included, all-zero row). Detail endpoint raises `InstructorProfile.DoesNotExist` → 404 for a non-affiliate. `EXPERT_CONTENT_ACTIVITY_ROLLUP.md` (raw content counts only) remains unbuilt; this feature computes those counts inline rather than depending on it.

The `partner/` URL segment scopes these to the partner-institution audience, leaving room for a future `admin/` analytics surface under the same app. Institution scope reaches each entity through: `NidusCourse.partner_institution`, `Enrollment.course__partner_institution`, `Certificate.enrollment__course__partner_institution`, `Webinar.partner_institution`, `WebinarRegistration.webinar__partner_institution`, `InstructorProfile.affiliated_institution`. Summary is a fixed ~10 aggregate queries (conditional aggregation via `Count(filter=Q(...))` / `Avg`), independent of data volume; the `NidusCourse(partner_institution, status)` index (`idx_ncourse_inst_status`, migration `courses/0017`) backs the course counts. Webinar time-buckets (upcoming/live/completed) are classified in Python over the small published-webinar set to avoid DB interval arithmetic.

Trends: `build_time_series(qs, date_field, granularity, periods)` zero-fills every bucket in Python (SQL returns only non-empty buckets) so the series is contiguous; `TruncMonth`/`TruncWeek` are tz-aware. `periods` clamped to `[1, 24]`, `top-courses` `limit` to `[1, 50]`.

**Access-denied:** every partner endpoint derives the institution from the token and takes no resource id, so the only failure is permission → **403** (`IsVerifiedPartnerInstitution`). Cross-institution data never leaks because every query filters by the caller's own institution.

**Admin (system-wide) surface** — `admin/` segment beside `partner/`, gated `[IsAuthenticated, IsEmailVerified, IsPlatformAdmin]` (plain `APIView`, **not** `AdminConsoleAPIView` — the admin SPA's JWT cookie from the shared login reaches it; no cross-app dependency). Logic in `analytics/services/admin_analytics_service.py`, views in `analytics/all_views/admin_analytics_views.py`. **Platform scope = no institution filter** — every query spans the whole platform. Admin service functions (`platform_summary`, `enrollment_trend`, `certificate_trend`, `user_signup_trend`, `revenue_trend`, `top_courses`, `conversion_funnel`) share names with the partner service, so admin views import them by **full module path**, not the flat `analytics.services` re-export (which stays partner-only to avoid a name clash). Endpoints:

| Endpoint | View | Purpose |
|---|---|---|
| `GET admin/summary/` | `AdminAnalyticsSummaryView` | Platform KPIs: users (total/by_type/active/verified/growth), courses (status breakdown + weighted rating), enrollments (active/completed/completion-rate/free-vs-paid/growth), certificates, webinars, **revenue** |
| `GET admin/users/trend/` | `AdminUserTrendView` | New-signup series (`User.registration_date`) |
| `GET admin/enrollments/trend/` | `AdminEnrollmentTrendView` | Enrollment series (`Enrollment.created_at`) |
| `GET admin/certificates/trend/` | `AdminCertificateTrendView` | Certificate series (`Certificate.issued_at`) |
| `GET admin/revenue/trend/` | `AdminRevenueTrendView` | Paid-order gross **sum** per bucket (`build_value_series`, not count) |
| `GET admin/top-courses/` | `AdminTopCoursesView` | Ranked platform courses (`?sort=`/`?limit=`) |
| `GET admin/funnel/` | `AdminFunnelView` | Distinct-learner funnel: signup → enrolled → completed → certified |

`build_value_series(qs, date_field, agg_expr, granularity, periods)` (`analytics_service.py`, sibling of `build_time_series`) powers the revenue trend — sums an aggregate per bucket instead of counting rows, floats the value for JSON. Admin access-denied is **403** only (no resource id; `IsPlatformAdmin`).

**Two documented gaps — do not fake either. Revenue is now enabled for admin but NOT for partner:**
- **Partner** `revenue` → `{'enabled': False, 'estimated_gross': None}`. Institution revenue needs per-institution attribution / payout (Payments Phase 2), not yet built.
- **Admin** `revenue` → `{'enabled': True, 'currency': 'BDT', 'gross', 'paid_orders', 'by_item_type', 'this_window', 'growth_pct'}` — real, summed from `payments.Order` where `status='paid'`. At platform scope there is no attribution problem (admin sees every order), so it is computed for real.
- `webinars.attendance_rate` (partner only) is computed but `attendance_tracking_enabled: False` until the live-day join flow populates `WebinarRegistration.attended` / `joined_at` (reserved).

See `docs/future_implementations/ANALYTICS_DASHBOARD.md` (plan), `docs/architecture/20-analytics-dashboard.md`, and `docs/api-testing/postman-analytics.md`.

### Payments (`payments/`, `/api/v1/payments/`)

SSLCommerz **hosted-redirect** checkout (gwprocess v4, sandbox via `SSLCOMMERZ_SANDBOX`), BDT only. Purchase targets: **course or webinar** — `Order` has nullable `course`/`webinar` FKs with a check constraint requiring exactly one (`order.item` / `order.item_type` resolve it). One `Order` row per gateway session (`payment_orders`): `initiated → processing → paid | failed | cancelled`; **PAID is terminal** (fail/cancel callbacks no-op on it). `amount` snapshots the target's `price` at checkout — validation is compared against the snapshot, never the live price. Partial uniques `(user, webinar)`, and (course) `(user, course) WHERE status='paid' AND schedule IS NULL` / `(user, schedule) WHERE status='paid' AND schedule IS NOT NULL` — mirrors `Enrollment`'s split so a learner can hold a self-paced PAID order and a cohort-seat PAID order for the same course simultaneously, or buy into a later cohort. Re-checkout cancels stale pending orders (scoped by the same `course`+`schedule` pair) and issues a fresh `tran_id`.

**Cohort seat checkout:** `Order.schedule` (nullable FK → `CourseSchedule`) is set only for course checkouts; `None` = self-paced (byte-identical to the pre-cohort behavior). `POST checkout/` accepts optional `{"schedule_id": N}` alongside `course_slug` (400 if paired with `webinar_slug`). `_guard_schedule_checkout` (`order_service.py`) runs advisory checks at checkout time (schedule belongs to the course, `status=scheduled`, enrollment window open, seat count) — non-locking, since the gateway round-trip can take a while; the authoritative, lock-protected check is `enroll_learner`'s `_assert_schedule_enrollable`, which runs again at `finalize_payment` time via `_grant_access(order)` passing `schedule=order.schedule`. A seat can still fill between checkout and payment completion — `finalize_payment` is the source of truth, same pattern as the free cohort-enroll path.

**Trust model — the core rule:** redirect/IPN bodies are never trusted. `finalize_payment(tran_id, val_id)` (`payments/services/order_service.py`) is the **only** path to PAID: it re-queries the SSLCommerz Validation API and verifies `status ∈ (VALID, VALIDATED)` + `tran_id` + `amount` + `currency` + `store_id` against the order. Mismatch → order FAILED + 422, no access granted (the FAILED write is persisted *outside* the rolled-back transaction — see `_ValidationRejected`). Success → PAID + `_grant_access` in the same transaction (course → `enroll_learner(..., enrollment_type=PAID, allow_unpublished=True)`; webinar → `register_for_webinar(..., via_payment=True)`) + `PAYMENT_SUCCESSFUL` on commit. Idempotent under double IPN / redirect+IPN races (pre-check + `select_for_update(of=('self',))` re-check — `of=('self',)` because the nullable target FKs LEFT-JOIN and Postgres can't `FOR UPDATE` the nullable side). Duplicate payment (second session paid) → FAILED + `gateway_payload.requires_refund=True` + `logger.critical` (manual refund; Phase 2 automates).

Endpoints: `POST checkout/` (learner-gated; body: **exactly one** of `{course_slug}` / `{webinar_slug}`, plus optional `{schedule_id}` for a course cohort seat → `gateway_url` + `item_type` + `schedule_id`) · `POST ipn/`, `GET/POST success|fail|cancel/` (**`AllowAny` + `authentication_classes=[]`** — gateway posts carry no JWT; the success redirect runs finalize and 302s to `FRONTEND_URL + FRONTEND_PAYMENT_*_PATH`) · `GET orders/[<pk>/]` (own-only; numeric id → 404 on no-access; `gateway_payload`/`val_id` never serialized; `schedule_id` included). The **success redirect is the primary finalize path** (local sandbox has no public IPN URL); IPN is the production safety net.

**Free-enroll / free-register gates:** `CourseEnrollView` rejects `price > 0` courses with 422 unless the caller has a PAID order (then enrolls/reactivates as `enrollment_type=paid` — unenroll → re-enroll never double-charges). `register_for_webinar` mirrors it: `price > 0` without a PAID order → 422 → checkout; `via_payment=True` (finalize-only) bypasses the price/published/capacity checks — a validated payment is honored even over capacity (logged overshoot, never refused after money moved). `enroll_learner` takes keyword-only `enrollment_type` (default FREE) and `allow_unpublished` (payment-finalize only); paid reactivation upgrades the type, free calls never downgrade it. Events `PAYMENT_SUCCESSFUL`/`PAYMENT_FAILED` follow the standard 4-edit notification wiring (context keys are `item_type`/`item_title`/`item_slug` — target-agnostic).

**Hardening (all live):** (1) **Reconciliation reaper** `reap_stale_processing_orders_task` (Celery beat, 15 min) resolves orders stranded in `processing` by querying the gateway (`query_transaction`) → finalize / fail / abandon-after-24h; no order stays pending forever. (2) **Callback signatures** — fail/cancel callbacks require a valid SSLCommerz `verify_sign` (`verify_callback_signature`); unsigned/forged ones are ignored (the success path is guarded by the API re-validation instead). (3) **`store_id` fails closed in production** — the check is skipped only when `SSLCOMMERZ_SANDBOX=True`. (4) **Race-safe duplicate** — the second concurrent PAID save trips the partial-unique, is caught as `IntegrityError`, and routed to `_record_duplicate_payment` (`requires_refund`, access still granted). (5) **IPN** returns 503 on transient gateway errors (SSLCommerz retries), 200 on permanent rejections. **Never `logger.info` in the payments app** — warning/error/critical/exception only. Institution wallet/payout, refunds, and the analytics `revenue.enabled` flip are **Phase 2 — not built**. See `docs/architecture/21-payments.md` and `docs/api-testing/postman-payments.md`.

### Webinars (`webinars/`, `/api/v1/webinars/`)

Live webinars owned by **verified partner institutions** — metadata + an external meeting link (Zoom/Meet/Jitsi), **not** a curriculum tree. `Webinar` (`webinars/all_models/webinar_models.py`) inherits `AuthoredModel` and mirrors `NidusCourse`'s review state machine, but presenters live on the one model. `clean()` enforces `created_by.user_type == 'partner_institution'`.

**Presenters — three distinct roles, do not conflate:**
- `host_expert` (FK→User, single) — the assigned lead host. Set via the dedicated `POST/DELETE /<pk>/host/` endpoint (`WebinarHostView`). **Required before publishing** (`_validate_webinar_completeness`) and the only actor who may publish. Must be an active affiliated expert of the owning institution.
- `institutional_speakers` (M2M→User) — additional platform experts credited as speakers. **Credit-only — no authoring rights** (unlike the course roster; webinar editing stays institution-only via `IsVerifiedPartnerInstitution`). Set in the create/update payload via the write-only `institutional_speaker_ids` list; **full replace** (`[]` clears, omit leaves untouched). Each id validated by `set_institutional_speakers()` (`webinars/services/webinar_service.py`) to be an active affiliated expert of the owning institution — reuses `_get_active_expert_user` (the same rule as host + course roster), foreign/inactive/unknown → `WebinarError(422)`. Overlap with `host_expert` is allowed.
- `guest_speakers` (JSONField) — external presenters with **no platform account**: list of `{full_name, title, bio}`, validated by `GuestSpeakerSerializer`. Set in the same create/update payload, mixes freely with institutional speakers.

Rule: presenter with an account → FK/M2M; no account → JSON. Read serializers (`WebinarSerializer`, `CatalogWebinarDetailSerializer`) expose `institutional_speakers` as nested `InstructorBriefSerializer`; loaders `prefetch_related('institutional_speakers')` to avoid N+1. `WebinarError` raised inside `serializer.save()` is caught by the create/patch views and returned in the standard envelope with `exc.http_status`.

**Status machine** (`Webinar.transition_to(new_status, actor=None)`): three states, **no approval gates** — the assigned host expert publishes directly. `draft → published` (host `POST /<pk>/publish/`, `WebinarPublishView`, scoped to `host_expert=request.user` → institution user gets 404), `published → archived` (`/archive/`, owner/host/admin), `archived → draft` (`/rework/`, owner/host). Publishing runs `_validate_webinar_completeness` (title, description, future `scheduled_at`, `duration_minutes`, `meeting_url`, host assigned). `is_editable()` = `draft|archived` (publishing freezes; rework reopens) — the state machine has **no `rejected` state**, so never add it back to `EDITABLE_STATUSES`. There is **no** institution-forward or admin-review step, and no `rejection_reason` field — do not reintroduce them. Slug endpoints (catalog/register/my-webinars) → 403 on no-access; numeric-ID endpoints → 404. `meeting_url` is registrant-only (never in catalog). See `docs/api-testing/postman-webinars.md` and `docs/architecture/19-webinars.md`.

**Editing scope** (`WebinarDetailView`, `/<int:pk>/`): GET is visible to the owning institution **or** the assigned host (`Q(created_by) | Q(host_expert)`); PATCH is **institution-only** — `_get_webinar(..., owner_only=True)` scopes to `Q(created_by)` so a host expert patching metadata → 404. The host's only mutating power is `/publish/`. Do not widen PATCH to the host.

**Registration + capacity** (`register_for_webinar`, `webinars/services/registration_service.py`): learner-only, published-only; reactivates a cancelled row (unique `(user, webinar)`) rather than duplicating. When `max_capacity is not None` it takes `Webinar.objects.select_for_update()` on the webinar row **before** counting active registrations — otherwise two first-time registrants (neither holds a row to lock) both pass the capacity check and over-subscribe.

**Notifications**: `WEBINAR_PUBLISHED` (institution + host, category `COURSE_MANAGEMENT`) and `WEBINAR_REGISTERED` (learner, category `COURSE_ACTIVITY`), both dispatched via `transaction.on_commit`. A `webinar.*` event needs **four** edits or it half-works: `NotificationEventType`, a builder in `notifications/services/builders.py`, `EVENT_TO_CATEGORY` in `preference_service.py` (else email preference can't be honored — falls through to always-send), and `_EVENT_TEMPLATE_MAP` in `notifications/email_utils.py` with an `emails/webinar_*.html` template (else the email task logs `no template …, skipping`).

### Permissions (core/permissions.py)

Custom DRF permission classes used across views:

- `IsPlatformAdmin` — `is_staff` or `user_type == admin`; used by admin-only actions like course review
- `IsEmailVerified` — gates most authenticated endpoints
- `IsInstructorUser` — `user_type == instructor` (verification not required)
- `IsPartnerInstitutionUser` — `user_type == partner_institution` (verification not required)
- `IsVerifiedInstructor` — instructor with approved `IdentityVerification`
- `IsVerifiedPartnerInstitution` — partner institution with `is_verified=True` and `is_active=True` on their profile
- `IsCourseCreator` — passes for either `IsInstructorUser` OR `IsPartnerInstitutionUser`; **unverified** analog of `IsVerifiedCourseCreator`, used on course/section/lecture/quiz/assignment/coding-exercise/content authoring endpoints so a course can be built and tested before identity verification completes
- `IsVerifiedCourseCreator` — passes for either `IsVerifiedInstructor` OR `IsVerifiedPartnerInstitution`; no longer gates day-to-day authoring — reserved for endpoints that must stay verified-only: leaving draft (`CourseSubmitForReviewView` and the institution-review flow in `status_views.py`), schedule mutations (`schedule_views.py`), and course-instructor invites (`invite_views.py`)
- `IsCourseInstructor` — object-level: user is in `course.instructors.all()`
- `IsRecentlyAuthenticatedAdmin` — extends `IsPlatformAdmin` with a session-freshness check (`session['admin_login_at']` ≤ `ADMIN_REAUTH_MAX_AGE`); for sensitive admin-console actions (see *Admin Console* below)

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

### Course Q&A / Discussion System

Per-course discussion board (`courses/all_models/discussion_models.py`) — **enrolled learners** ask questions, discuss, and get replies; the course's **instructors** answer. No public/guest surface. Modeled after the review system (file layout, service-gate, denormalized counters, `DiscussionError(http_status)`) and messaging (enrolled-gate in service, two-level threading, soft delete). Full design in `docs/architecture/26-discussion-qa.md`.

**Models:** `CourseQuestion` (FK `course`/`author`, nullable `related_content` FK→`SectionContent` with `SET_NULL`, `title`, `body`, `is_pinned`, `is_deleted`, denormalized `reply_count`/`upvote_count`), `QuestionReply` (FK `question`/`author`, `body`, denormalized `is_instructor_reply` badge, `is_deleted`, `upvote_count`). `related_content` points at `SectionContent` (the content-ordering abstraction, GFK to Lecture/Quiz/Assignment/Coding) so a question can anchor to any content type; null = general course question. **Upvotes are counter-only** — `upvote_count` is a plain denormalized integer, no per-user vote table; `POST .../upvote/` is an atomic `F()+1`. Deliberate MVP simplification: no dedup (repeatable), no un-upvote, no `viewer_upvoted` flag. Reintroduce a per-(voter,target) vote table (mirror `ReviewVote`) if those become requirements. Because nothing dedups and the counter drives `?ordering=-upvote_count`, both upvote endpoints carry `DiscussionUpvoteThrottle` (`DISCUSSION_UPVOTE_RATE_LIMIT`, default `30/min`, mirrors `AdminActionThrottle`) — a brake on rank inflation, not a substitute for the vote table.

**Access — service layer only, no `IsEnrolled` permission.** A pure enrolled-gate would lock out instructors, who must participate. Views carry only `[IsAuthenticated, IsEmailVerified]`; the gate is `discussion_service._assert_access(user, course)` — grants an active enrolled learner OR a course instructor (`course.instructors` or `created_by`) OR a platform admin. Admin and `created_by` are checked first from already-loaded columns (no query); everyone else falls through to `learner_service.resolve_course_access`, which **already evaluates the roster** — never add a second `course.instructors.filter(...).exists()` on top of it. Returns `is_instructor` so the service also gates instructor-only actions (pin, delete-any) and stamps `is_instructor_reply`. **403-vs-404:** slug entry (`<slug>/questions/`) → 403; numeric-ID entry (question/reply/vote/pin) → 404.

**Endpoints** (`/api/v1/courses/`): `GET/POST <slug>/questions/`, `GET/DELETE questions/<id>/`, `POST questions/<id>/replies/`, `POST questions/<id>/pin/` (instructor), `POST questions/<id>/upvote/`, `DELETE replies/<id>/`, `POST replies/<id>/upvote/`. List filters (allow-listed, service layer): `?content_id=`, `?ordering=` (`-created_at`|`created_at`|`-upvote_count`|`-reply_count`); pinned always sorts first. Deletes are **soft** (`is_deleted`). Upvote is a plain atomic `F('upvote_count')+1` increment (counter-only — see Models above).

**Notifications** (in-app only, `skip_email=True`, on `transaction.on_commit`): `QUESTION_POSTED` → course instructors (minus asker); `QUESTION_REPLIED` → question author + prior participants (minus replier). Three-edit wiring only (no email template needed): `NotificationEventType` + builder + `_BUILDERS`, plus `EVENT_TO_CATEGORY` (both → `COURSE_ACTIVITY`).

### Learner Dashboard, Wishlist & Notes

Learner-facing surface above course consumption, all in `courses` under `/api/v1/courses/`, all gated `[IsAuthenticated, IsEmailVerified, IsLearnerUser]`. Full design in `docs/architecture/27-learner-dashboard.md`.

**Four aggregates, no new models** (`courses/services/dashboard_service.py`, views in `all_views/dashboard_views.py`): `GET learner/dashboard/summary/` (KPI tiles), `learner/activity/` (feed), `learner/upcoming/` (cohort/drip/webinar dates), `learner/continue/` (resume target). Plus `GET my-certificates/` (extends the existing certificate trio).

**Two honesty rules — do not "fix" these by inventing numbers.** (1) `total_xp` is **absent from the summary response entirely**, not zero: it is not derivable from any table, any formula is retroactively unstable, and it cannot back an XP timeline or leaderboard. Adding it means adding a `LearnerXpEvent` ledger first — `LearnerActivityDay` does **not** serve that purpose (see below). (2) `total_learning_seconds` sums *furthest-cursor* positions, not accumulated playback — re-watching does not increase it; documented in the service docstring.

**`LearnerActivityDay` backs the day streak** (`courses/all_models/activity_models.py`, service `activity_service.py`). One row per learner per day they studied, `uq_activity_day_user_date`. It replaced a union over four consumption tables that could not be made accurate: `WatchProgress.last_watched_at` is `auto_now`, so re-opening a lecture *overwrote* the date it carried, re-reading a finished article recorded nothing (its mark-complete button is gone by then), and a coding Run persists nothing at all. `record_learner_activity(user)` is the **only** writer — called from `learner_service.py` inside each loader's `if not is_instructor:` branch and from every submit path, never from a view, never for instructor preview. It never raises: bookkeeping must not turn a working lecture fetch into a 500. Opening course content counts; browsing the dashboard/catalog does not; enrolling does not. Day-granular on purpose — the video player POSTs progress every few seconds, so event-granularity would mean thousands of rows per lecture. **Do not repurpose this as the XP ledger**: XP needs one row per scoring event with a points value, the opposite de-duplication rule. `day_streak_is_approximate` is now `False`, kept in the response only so it can flip back if per-user timezones ever land (days bucket in the platform-wide `TIME_ZONE`). Migration `0030` backfills history best-effort.

**Activity feed** is a Python `heapq.merge` k-way merge of six per-source querysets, each `select_related` and capped at `ACTIVITY_WINDOW = 200` — exactly 6 queries regardless of page depth. `.union()` and raw SQL were both rejected (see the doc). Consequence to keep documented: paginated `count` is the window size, not lifetime activity.

**`learner/continue/` reuses `get_learner_enrollments` + `load_learner_curriculum`** — do not write a new curriculum traversal; the lock semantics live there. Returns **200 with `data: null`** when there is no active enrollment, never 404.

**`Wishlist`** (`courses/all_models/wishlist_models.py`, `db_table='course_wishlists'`) — thin `(user, course)` row, unique-constrained. `add_to_wishlist` is `get_or_create`: **201 first, 200 on repeat**, so a double-tapped heart is never an error. `is_wishlisted` on catalog cards is a **pre-computed id set passed in serializer context** (`get_wishlisted_course_ids`, resolved *after* pagination), read by `_WishlistFlagMixin`, defaulting to `False` when the context key is absent — one query per page, never per row, and no behaviour change for anonymous callers or the nested card inside `EnrollmentSerializer` (which now carries `course.is_wishlisted: false`). Never swap this for an `Exists()` annotation on `filter_catalog_courses` — that queryset builder must stay auth-agnostic.

**`LearnerNote`** (`courses/all_models/note_models.py`) — private note optionally anchored to a course, lecture and playback timestamp. `tags` is a GIN-indexed `JSONField` (not `ArrayField` — every list field in this codebase is JSON); `color` is a `TextChoices` enum (not free-form hex — that is a style-injection surface); `course`/`lecture` are `SET_NULL` (notes are the learner's own work). **Enrollment is not required** to file a note — a published course is enough. Detail endpoint returns **404, never 403**, on another learner's note, for all three verbs.

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

**Review queues:** `GET /admin/pending-review/` (`CourseAdminPendingReviewListView`, `IsPlatformAdmin`) lists all `status=under_review` courses, oldest-submitted-first, paginated — the admin's discovery surface for `/review/` (previously there was no way to browse the queue; an admin had to already know a course's `pk` from the `COURSE_SUBMITTED` notification). `GET /institution-review-queue/` (`CourseInstitutionReviewQueueView`, `IsVerifiedPartnerInstitution`) is its institution-side mirror — lists the caller's own `status=institution_review` courses, scoped by `partner_institution=request.user.partner_institution_profile`. Neither endpoint takes a course id; access is derived entirely from the caller. Both accept an optional `?delivery_mode=self_paced|scheduled` filter (`_filter_by_delivery_mode()`, shared helper in `status_views.py`) — an unrecognized value → `400`.

Leaving `draft` (to either `under_review` or `institution_review`) runs `_validate_course_completeness()`: checks title/description, all videos `status=ready`, all quizzes have questions with correct answers, plus a `delivery_mode`-dependent curriculum check — see *Scheduled Courses (Cohorts)* below for the self-paced-vs-scheduled split. `institution_review` is **not** editable (content frozen, like `under_review`).

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
- `UserLoginView` (`POST /api/v1/auth/login/`) sets the tokens **only** as HttpOnly cookies via `set_jwt_cookies()` (`authentication/utils/cookie_helpers.py`) — the JSON response body never contains `access`/`refresh` (only `user_id`/`email`/`full_name`/`user_type`/`is_email_verified`). A browser client cannot read the token from JS.
- Protected endpoints authenticate from **either** the `Authorization: Bearer` header **or** the `access_token` cookie. `CookieJWTAuthentication` (`authentication/authentication.py`) is registered first in `DEFAULT_AUTHENTICATION_CLASSES`, then header-based `JWTAuthentication`. Cookie name is `JWT_ACCESS_COOKIE_NAME` (default `access_token`) — keep the auth class and `cookie_helpers.py` reading the same setting.
- Token refresh: `POST /api/v1/auth/token/refresh/`
- **WS token bridge:** `GET /api/v1/auth/ws-token/` (`WsTokenView`, `IsAuthenticated` + `IsEmailVerified`) returns `{'data': {'token': str(request.auth)}}` — the same access token that authenticated the request (via cookie or header), just handed back as JSON. Exists solely because the `/ws/` WebSocket handshake takes the JWT as a `?token=` query param and cannot read the HttpOnly cookie; the frontend calls this once (cookie-authenticated) to get a string it can put in the WS URL. Does not mint a new token — just echoes the validated one back.
- Logout (`POST /api/v1/auth/logout/`): blacklists the refresh token (**ownership-checked** — a caller can only blacklist a token whose `user_id` matches them; else 400) + clears JWT cookies + flushes the Django session if one exists (admins). A stateless access token can't be revoked — it lives until expiry.
- OAuth: authorization-code flow for Google and LinkedIn; callback URLs configured via env vars

### Admin Console (session auth) — `admin_console/`, `/api/v1/admin-console/`

Platform back-office. First slice is a **session-based admin login**, separate from the JWT flow — a browser back-office wants short idle timeouts + CSRF-protected writes, which don't fit the 12 h JWT. JSON API (SPA renders the page). Owns one model, `AdminSession` (device/session tracking — see below); admin auth itself still rides the existing `django_session` table.

**Core rule — session auth is per-view, never global.** DRF `SessionAuthentication` enforces CSRF only when it authenticates a user; adding it to `DEFAULT_AUTHENTICATION_CLASSES` would force a CSRF token onto every existing JWT `POST`/`PATCH` (a breaking change). It lives **only** on `AdminConsoleAPIView` (`admin_console/all_views/base.py`): `authentication_classes = [SessionAuthentication, CookieJWTAuthentication, JWTAuthentication]` (session-primary, JWT fallback for tooling) + `[IsAuthenticated, IsEmailVerified, IsPlatformAdmin]`. **Every future admin-console endpoint subclasses `AdminConsoleAPIView`** — never re-enable session auth globally.

**Login is the shared platform login — the admin console has no login endpoint of its own.** The common `POST /api/v1/auth/login/` (`UserLoginView`) is the single login for every role and still returns JWT; when the user is an admin (`is_staff or user_type == 'admin'`) it *additionally* calls `django_login`, stamps `session['admin_login_at']`, and primes `csrftoken` via `get_token()` — one login yields JWT **plus** `sessionid` + `csrftoken`, so the back-office needs no separate admin login. Non-admins get JWT only (no session). `UserLoginView` is `AllowAny` with no `SessionAuthentication`, so this doesn't force CSRF on the login POST. (There is **no** dedicated `admin-console/auth/login/` or `auth/csrf/` — they were removed as redundant; the shared login primes CSRF.)

**Logout is also the shared endpoint** — the admin console has no login *or* logout of its own; it exposes only `GET auth/session/` (who-am-I: profile + `idle_timeout_seconds`, subclasses `AdminConsoleAPIView`). A non-admin logging in gets no session, so admin-console endpoints reject them with **403**. **Logout is symmetric with login:** the shared `POST /api/v1/auth/logout/` (`LogoutView`) calls `django_logout` when a session exists (in addition to blacklisting the refresh token + clearing JWT cookies), so it fully signs an admin out — otherwise the `sessionid` would outlive logout. JWT-only clients have no session → no-op. (A stateless access token can't be revoked either way — it lives until expiry.) Idle timeout = `SESSION_SAVE_EVERY_REQUEST=True` + `SESSION_COOKIE_AGE=ADMIN_SESSION_IDLE_TIMEOUT` (sliding). Step-up re-auth is **available** via `IsRecentlyAuthenticatedAdmin` but **not currently applied** to any endpoint. 2FA deferred (no lib). See `docs/architecture/24-admin-console-auth.md` and `docs/api-testing/postman-admin-console.md`.

**Device/session tracking + remote logout.** `AdminSession` (`admin_console/all_models/session_models.py`) records one row per admin browser/device, keyed to a `django_session` row by `session_key`, storing IP + raw `user_agent` + parsed `browser`/`os`/`device` (`user-agents` lib). Capture is centralized in a `user_logged_in` receiver (`admin_console/signals.py`, wired via `AdminConsoleConfig.ready()`) — it no-ops for non-admins and for JWT clients (no `django_login` → no `session_key`), and `update_or_create`s per key. A `user_logged_out` receiver drops the row. `AdminConsoleAPIView.initial()` best-effort-touches `last_seen_at` on every admin-console request (never breaks the request). Endpoints (all subclass `AdminConsoleAPIView`, own-sessions only): `GET sessions/` (list live sessions, `is_current` flag; prunes rows whose `django_session` expired), `DELETE sessions/<int:pk>/` (revoke one — deletes the `django_session` row via `SessionStore(key).delete()` + the record; numeric id → **404** on not-own, per the 403-vs-404 rule), `POST sessions/revoke-others/` ("log out everywhere else"). Never trust a client-supplied `session_key`/IP/UA — all captured server-side from `request`.

**User management + audit log.** Admin administration of accounts, in `admin_console/services/user_admin_service.py` (`AdminUserActionError(message, http_status)` mirrors `ScheduleError`). Endpoints (subclass `AdminConsoleAPIView`; numeric id → **404**): `GET users/` (search/filter/sort, paginated — `?search=` email/name icontains, `?user_type=`, `?is_active=`/`?is_restricted_by_admin=`/`?is_verified=`/`?is_email_verified=`, `?include_deleted=`, whitelisted `?sort=`), `GET users/<int:pk>/` (detail, `all_with_deleted()` so admins can inspect soft-deleted accounts), `POST users/<int:pk>/suspend/`, `POST users/<int:pk>/reactivate/`, `POST users/<int:pk>/role/` (`{user_type?, is_staff?}`), `GET audit/` (paginated audit log; `?target_user_id`/`?actor_id`/`?action`). **Suspend sets BOTH `is_restricted_by_admin=True` and `is_active=False`** — the first blocks new logins (all login paths check it), the second additionally kills existing JWT access tokens (SimpleJWT rejects inactive users on the next request; it does **not** re-check `is_restricted_by_admin`). Suspend also **blacklists every outstanding refresh token** (`authentication/services/token_service.py:blacklist_all_refresh_tokens` — the shared helper, also used by password change/reset; the old `authentication/serializers._blacklist_all_tokens` is now a thin re-export) so the user can't mint a fresh access token via `/token/refresh/`; the blacklist runs **inside** the suspend transaction (rolls back with it). Suspend/reactivate each dispatch an `ACCOUNT_SUSPENDED`/`ACCOUNT_REACTIVATED` notification (email + in-app) via `transaction.on_commit` — these two events are **deliberately absent from `EVENT_TO_CATEGORY`** (critical account notices → the `get_email_preference` fallback always emails them, unmutable). Because suspend sets `is_active=False`, `ACCOUNT_SUSPENDED` is also in `send_notification_email_task`'s `_EMAIL_INACTIVE_ALLOWED_EVENTS` allowlist — otherwise the task's inactive-recipient skip would silently drop the suspension email (a hard-deleted account is still never emailed). Guards: can't suspend self or another admin, can't double-suspend (all 422). **Reactivate only lifts an admin suspension** — its guard keys on `is_restricted_by_admin`, so a user deactivated for a non-suspension reason is not silently re-activated (422 "not suspended"). **Role change** flips `user_type` and/or `is_staff`; switching `user_type` **provisions the target profile** via `authentication/services/profile_service.py` → `ensure_profile_for_type(user)` (the create-time signal no longer fires post-creation) and leaves the old type's profile dormant (deleting it would cascade real content). Can't change own role (422). All endpoints (reads + the three mutations) use the **base admin gate** (`AdminConsoleAPIView`: session or JWT + `IsPlatformAdmin`); session-authed writes still need `X-CSRFToken`. Every mutation writes an append-only `AdminActionLog` row (actor/target/action/reason/before-after metadata **plus snapshotted `actor_email`/`target_email`** so attribution survives account deletion) in the same transaction; mutations take a `select_for_update` lock on the target (no duplicate/interleaved audit rows) and are throttled per-admin (`ADMIN_ACTION_RATE_LIMIT`, default `30/min`). `is_staff` is strictly parsed (a stringy `"false"` is rejected, never coerced truthy). Search requires ≥2 chars and is backed by `pg_trgm` GIN indexes on `User.email`/`full_name` (migration `authentication/0004`, built `CONCURRENTLY`). `pg_trgm` is already a project dependency (`courses` uses trigram indexes on `NidusCourse`), so no new extension burden. (`IsRecentlyAuthenticatedAdmin` exists in `core/permissions.py` for step-up re-auth but is **not** currently applied here.) `ensure_profile_for_type` is the **single source of truth** for profile provisioning — the `authentication/signals.py` create-signal now calls it too. **Deferred:** support tickets/disputes (own feature) and the platform-wide (all-apps) audit log. (Suspension-notification email and refresh-token blacklisting on suspend are **done**.)

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

**Errors DRF raises before your view runs** — throttling, `NotAuthenticated` (401), `permission_classes` denials (403), `MethodNotAllowed` (405), parse errors — never reach the code above; DRF renders them as a bare `{'detail': '...'}`. `core/exception_handlers.py` → `envelope_exception_handler` (wired as `REST_FRAMEWORK['EXCEPTION_HANDLER']`) rewraps them into the same `success`/`message` envelope, keeping the original `detail` key alongside so older clients don't break. A DRF `ValidationError` becomes `{'success': False, 'message': 'Validation failed.', 'errors': {...}}`, matching what views build by hand. Unhandled (non-DRF) exceptions pass straight through — the per-view try/except still owns the 500 path. **Views are unaffected**: a view that returns its own envelope is never rewritten, because the handler only sees *raised* exceptions.

> ⚠️ `FRONTEND_ERROR_RESPONSE_FORMAT.md` describes a **different product** (NidusJob — `type`/`title`/`instance`/`trace_id`/`_legacy` fields, `api.nidusjob.com` URIs). None of that shape exists in this codebase. The real, universal contract is the `success`/`message`/`errors` envelope documented above. Treat that file as stale until it is rewritten or deleted.

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
| `SSLCOMMERZ_STORE_ID` / `SSLCOMMERZ_STORE_PASSWORD` | Sandbox store credentials from developer.sslcommerz.com |
| `SSLCOMMERZ_SANDBOX` | `True` → sandbox base URL; `False` → live `securepay` host |
| `BACKEND_URL` | Public base URL used to build gateway callback URLs (IPN needs it reachable) |
| `FRONTEND_PAYMENT_SUCCESS_PATH` / `_FAIL_PATH` / `_CANCEL_PATH` | Frontend paths the payment callbacks 302 to |

For local dev, `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` prints OTP emails to the terminal instead of sending them.

## Docs

Detailed design rationale is in `docs/architecture/` (23 numbered design docs, `01`–`22` plus `24`, plus a `README.md` guide map). `01-system-overview.md` is the at-a-glance tour (apps, layers, request lifecycle) and is worth reading before making structural changes. `09-coding-exercises.md` covers the coding exercise data model, authoring API, and design decisions. `14-certificate-system.md` covers the completion certificate issuance flow, PDF generation, and public share URLs. `15-review-rating-system.md` covers the review/rating data model, vote atomicity, denormalized catalog fields, and access-denied policy. `16-notification-system.md` covers the notification dispatcher, event types, and WebSocket delivery. `17-messaging-system.md` covers the messaging data model, REST + WebSocket protocol, unread semantics, and frontend client contract (see also `docs/api-testing/postman-messaging.md`). `18-partner-institutions.md` covers institution verification, expert onboarding, departments, and course creation + roster assignment; `docs/api-testing/postman-partner-institution.md` is its manual-test guide. `19-webinars.md` covers institution-owned webinars — presenter roles, publish state machine, catalog + registration, and notification wiring; `docs/api-testing/postman-webinars.md` is its manual-test guide. `20-analytics-dashboard.md` covers the partner-institution analytics dashboard — metrics, institution-scoping, query strategy, and the revenue/attendance caveats; `docs/api-testing/postman-analytics.md` is its manual-test guide. `21-payments.md` covers the SSLCommerz payment integration — order state machine, validation trust model, callback topology, and edge-case policies; `docs/api-testing/postman-payments.md` is its sandbox walkthrough. `22-scheduled-courses.md` covers cohort schedules — the schedule state machine, cohort enrollment, drip release, and learner gates; `docs/api-testing/postman-schedules.md` is its manual-test guide. `24-admin-console-auth.md` covers the session-based admin login — the per-view session-auth rule, CSRF flow, idle/re-auth model, and the 403-vs-400 login split; `docs/api-testing/postman-admin-console.md` is its manual-test guide. `26-discussion-qa.md` covers the course Q&A / discussion board — data model, the service-layer enrolled-or-instructor access gate, upvote toggle, and notification wiring; `docs/api-testing/postman-discussion.md` is its manual-test guide. `docs/CHANGELOG_LEARNER_DASHBOARD.md` is the file-by-file record of that work (phases, rationale per file, and the three completion bugs it uncovered). `27-learner-dashboard.md` covers the learner dashboard aggregates, certificates list, wishlist, and notes — the honesty rules on the summary endpoint (why `total_xp` is absent), the `LearnerActivityDay` streak ledger and what does and does not count as studying, the k-way-merge activity feed, the `is_wishlisted` context mechanism, and the note model's field-type decisions; `docs/api-testing/postman-learner-dashboard.md` is its manual-test guide. `25-admin-capabilities.md` is the cross-cutting map of **all** platform-admin capabilities (admin console + course review + identity/institution verification review + course-category CRUD + platform analytics), the two elevation mechanisms (`IsPlatformAdmin` gate vs. inline `is_staff`/`admin` branch on archive/restore), and pointers to each area's deep doc. `docs/api-testing/postman-learner-journey.md` is a standalone learner-side walkthrough (catalog browse → free/paid/cohort enrollment via payment checkout → curriculum/lecture/quiz/assignment/coding consumption) — extracted from and superseding `POSTMAN_TESTING_GUIDE.md` §36's stale pre-payments enrollment note. `FRONTEND_ERROR_RESPONSE_FORMAT.md` is **stale and describes a different product** (NidusJob) — do not follow it; the error shape all views must follow is the `success`/`message`/`errors` envelope in *Response Format* above, with `core/exception_handlers.py` covering DRF-raised errors.
