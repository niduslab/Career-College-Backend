# Changelog — Scheduled Courses

File-by-file record of every change made to ship cohort scheduling. Grouped by phase. For the
feature explained end-to-end (not file-by-file), see `docs/architecture/22-scheduled-courses.md`.
For the original design plan, see `docs/future_implementations/SCHEDULED_COURSES.md`.

**Resolved (was a known gap):** `guard_editable()` used to check course-level state only — once a
schedule was `ongoing`, every section (including already-released ones) was editable. It now takes an
optional `section=` argument and blocks edits/deletes of a section already released to learners
(`unlocks_at` null or in the past) with a 422, while still allowing new content elsewhere. The carve-out
also widened to `scheduled`-or-`ongoing` (author ahead of `start_date`) and closes once every schedule is
`completed`/`archived`. See `docs/CHANGELOG_SELF_PACED_IMPACT.md` §9 and §4.

**Self-paced ripple effects:** for the changes this feature forced onto the pre-existing self-paced path,
see `docs/CHANGELOG_SELF_PACED_IMPACT.md`.

---

## Phase 1 — Data model + schedule management

### New files

| File | What it does |
|---|---|
| `courses/all_models/schedule_models.py` | Defines `CourseSchedule` — the cohort model. Holds the 5-state `status` field, the `VALID_TRANSITIONS` map, `transition_to()` (the only way to change status), and `_validate_activation()` (the checks run when moving `draft → scheduled`: course must be published, dates must be ordered correctly, close/start dates must be in the future). |
| `courses/services/schedule_service.py` | All schedule business logic. `ScheduleError` (message + HTTP status). `get_course_for_schedule_manage` / `get_course_for_schedule_read` decide who can mutate vs. only view a course's schedules (institution-only / creator-only for mutation; roster experts get read access). `get_schedule`, `get_course_schedules`, `delete_schedule` (draft-only), and thin wrappers `activate_schedule` / `archive_schedule` / `rework_schedule` around `transition_to`. |
| `courses/all_serializers/schedule_serializers.py` | `CourseScheduleSerializer` (read-only, nests `created_by`/`last_edited_by`) and `CourseScheduleCreateUpdateSerializer` (write; validates date ordering in `validate()`). |
| `courses/all_views/schedule_views.py` | The 5 HTTP endpoints: list/create, detail (get/patch/delete), activate, archive, rework. Each view resolves the course through the service (404 on no access), then calls the matching service function, translating `ScheduleError`/`ValidationError` into the standard response envelope. |
| `courses/all_tests/test_course_schedules.py` | Test suite for the whole feature (grows across all 3 phases). |
| `courses/migrations/0018_courseschedule_and_more.py` | Generated migration: creates `CourseSchedule` table, adds `CourseSection.unlocks_at`, adds `Enrollment.schedule`, swaps the old single unique constraint on `Enrollment` for two partial ones. |
| `docs/future_implementations/SCHEDULED_COURSES.md` | Original design doc — the plan this whole feature was built from. |

### Modified files

| File | What changed |
|---|---|
| `courses/all_models/course_models.py` | Added `CourseSection.unlocks_at` — nullable datetime. `NULL` = section is available immediately (default, matches every pre-existing section). A future datetime marks it as drip-locked. |
| `courses/all_models/enrollment_models.py` | Added `Enrollment.schedule` — nullable FK to `CourseSchedule`. `NULL` = classic self-paced enrollment (unchanged behavior). Replaced the single `unique(user, course)` constraint with two partial ones: one for self-paced rows (`schedule IS NULL`) preserving the old "one enrollment per course ever" rule, one for cohort rows (`schedule IS NOT NULL`) allowing a learner to join a *different* cohort of the same course later. |
| `courses/all_models/__init__.py` | Star-imports the new `schedule_models` module so `CourseSchedule` is reachable via `courses.models`. |
| `courses/services/__init__.py` | Re-exports everything from `schedule_service.py` so other modules can `from courses.services import ScheduleError, activate_schedule, ...`. |
| `courses/all_serializers/__init__.py` | Star-imports the new `schedule_serializers` module. |
| `courses/all_views/__init__.py` | Imports and re-exports the 5 new schedule view classes. |
| `courses/views.py` | Re-exports the schedule views (this file is the thin public entry point `urls.py` imports from). |
| `courses/urls.py` | Adds the 5 schedule URL patterns under `<int:pk>/schedules/...`. |
| `courses/tasks.py` | Adds `advance_course_schedules_task` — the Celery beat job. Every run: finds schedules with `status='scheduled'` whose `start_date` has passed and flips each to `ongoing`; finds `status='ongoing'` schedules whose `end_date` has passed and flips each to `completed`. Uses `transition_to()` per row (not a bulk `.update()`) so validation still runs, and wraps each row in its own try/except so one bad row can't stop the rest. |
| `career_college_backend/settings.py` | Registers `advance_course_schedules_task` in `CELERY_BEAT_SCHEDULE` to run every 5 minutes (300s). |
| `CLAUDE.md` | Adds the "Scheduled Courses (Cohorts)" reference section. |

---

## Phase 2 — Cohort enrollment + drip authoring

### Modified files

| File | What changed |
|---|---|
| `courses/services/enrollment_service.py` | Added `_assert_schedule_enrollable(schedule)` — locks the schedule row (`select_for_update`) then checks: status must be `scheduled`, `now` must be inside `[enrollment_opens_at, enrollment_closes_at]`, and if `max_seats` is set, counts active enrollments against it (the row lock happens *before* counting, so two learners racing for the last seat can't both get in). `enroll_learner()` gained a `schedule=None` keyword: when passed, it runs the above check and stamps `schedule` onto the created/reactivated `Enrollment` row. `schedule=None` (the default) behaves exactly as before. |
| `courses/all_views/enrollment_views.py` | `CourseEnrollView.post()` now reads an optional `schedule_id` from the request body, resolves it against `course.schedules` (404 if not found), and passes it through to `enroll_learner()`. |
| `courses/all_serializers/enrollment_serializers.py` | `EnrollmentSerializer` now includes the `schedule` field so API responses show which cohort (if any) an enrollment belongs to. |
| `courses/utils.py` | `guard_editable(course)` gained the carve-out described above: if the course isn't normally editable but is `published` *and* has at least one `ongoing` `CourseSchedule`, treat it as editable anyway. This is the single switch that allows drip content upload. |
| `courses/all_serializers/content_serializers.py` | `CourseSectionSerializer` and `CourseSectionCreateUpdateSerializer` both gained the `unlocks_at` field, so it can be set/read through the normal section create/update endpoints — no new endpoint needed for drip authoring. |

---

## Phase 3 — Learner release gates

### Modified files

| File | What changed |
|---|---|
| `courses/services/learner_service.py` | Added `ContentNotReleasedError` (a 422 exception — timing rule, not access-denied) and `assert_content_released(enrollment, section)`, which raises it in two cases: (1) the enrollment is cohort-bound and `now < schedule.start_date` ("This course has not started yet."), or (2) the section's `unlocks_at` is still in the future ("This content has not been released yet."). Called it inside all four consumption loaders (`get_consumption_lecture`, `get_quiz_for_consumption`, `get_assignment_for_consumption`, `get_coding_exercise_for_consumption`) right after the existing access check. Also changed `resolve_course_access` to prefer a learner's self-paced enrollment over a cohort one when they hold both (self-paced is more permissive, so it should win), and changed `load_learner_curriculum` to accept the caller's `enrollment` and annotate every section in its output with `is_locked` and `unlocks_at` — locked sections are still listed, never hidden, just marked. |
| `courses/all_views/learner_views.py` | `LearnerCurriculumView` now passes the resolved `enrollment` into `load_learner_curriculum`. The three GET detail views (lecture/quiz/assignment/coding — via the loaders) and the three write views that fetch their own enrollment inline (`LearnerLectureProgressView`, `LearnerQuizSubmitView`, `LearnerAssignmentSubmitView`) all now catch `ContentNotReleasedError` and return 422 through a shared `_not_released_response()` helper. The write views call `assert_content_released` explicitly since they don't go through the shared loaders. |

---

## Test coverage added

All in `courses/all_tests/test_course_schedules.py`:

- **State machine** — every valid/invalid transition, activation validation failures (unpublished course, bad date order, past dates).
- **CRUD + ownership** — solo instructor and institution full CRUD; roster experts read-only (404 on mutation attempts); cross-institution 404s with no existence leak; wrong user types get 403.
- **Edit policy** — PATCH allowed in `draft`/`scheduled`, blocked (422) once `ongoing`; delete blocked outside `draft`.
- **DB constraints** — duplicate self-paced enrollment rejected; two schedules of the same course both enrollable by one learner; duplicate cohort enrollment rejected.
- **Beat task** — flips due rows, leaves future/open-ended rows untouched, tallies correctly.
- **Cohort enrollment API** — happy path, unknown schedule, draft/window/capacity/duplicate refusals.
- **Drip authoring** — published+ongoing course accepts new sections; published+no-ongoing-schedule course still blocks edits.
- **Learner gates** — curriculum lock markers, open vs. locked lecture access, locked write refusal, pre-start blocks everything, instructor bypass, self-paced learners still respect drip locks.

46 tests total for this feature; full `courses` suite (330 tests) and `payments` suite (62 tests) re-run clean apart from one pre-existing unrelated failure (confirmed via `git stash` against clean `main`).
