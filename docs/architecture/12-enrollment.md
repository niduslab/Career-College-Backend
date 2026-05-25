# 13 — Learner Enrollment

## Overview

The enrollment system connects learners to published courses. It serves as the access-control gateway: without an active enrollment, a learner can browse the public catalog but cannot access lectures, quizzes, assignments, or coding exercises.

## Data Model

### Enrollment

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK → User | The learner (must have `user_type='learner'`) |
| `course` | FK → NidusCourse | Must be a published course |
| `enrollment_type` | CharField | `free`, `paid`, or `admin_granted` |
| `is_active` | BooleanField | `False` on unenroll (soft revoke, progress preserved) |
| `progress_percent` | PositiveIntegerField | Denormalized 0–100 completion percentage |
| `completed_at` | DateTimeField (null) | Set when `progress_percent` first reaches 100 |
| `last_accessed_at` | DateTimeField (null) | Updated each time the learner opens course content |
| `created_at` / `updated_at` | auto timestamps | Via `TimestampedModel` |

**Constraints:** `UniqueConstraint(user, course)` — one enrollment per learner per course (including inactive).

**Indexes:** `(user, is_active, -last_accessed_at)` for "My Courses" dashboard, `(course, is_active)` for course enrollment counts.

### Relationship to Existing Models

- `Enrollment.user` → `authentication.User` (learner)
- `Enrollment.course` → `courses.NidusCourse` (published)
- `WatchProgress` already tracks per-lecture completion — the enrollment service uses this to compute `progress_percent`.

## API Endpoints

### Public Catalog (no authentication required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/courses/catalog/` | Paginated published courses. Filters: `?category=`, `?level=`, `?language=`, `?search=` |
| GET | `/api/v1/courses/catalog/{slug}/` | Course detail with metadata, objectives, prerequisites, audiences. No curriculum content. |

### Enrollment Actions (authenticated learner)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/courses/{slug}/enroll/` | Create an enrollment. Reactivates if previously unenrolled. |
| POST | `/api/v1/courses/{slug}/unenroll/` | Soft-deactivate enrollment. Progress preserved. |

### My Courses Dashboard (authenticated learner)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/courses/my-courses/` | Paginated active enrollments with progress. |
| GET | `/api/v1/courses/my-courses/{slug}/` | Slim course-header payload: course metadata (title, description, instructors, objectives, totals) + the caller's enrollment status (`progress_percent`, `last_accessed_at`, `completed_at`) + `is_instructor` flag for preview. **No curriculum tree** — fetch that from `/learn/{slug}/curriculum/`. Allowed for the course's own instructor too (preview mode; `enrollment` is `null`). |

### Learner Consumption (Phase 1 + Phase 2)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/courses/learn/{slug}/curriculum/` | Sidebar outline: ordered sections + items with title, type, position. Lectures carry `lecture_type`, `duration_seconds`, and (for learners) an `is_completed` marker. No heavy payloads. |
| GET | `/api/v1/courses/learn/lectures/{lecture_id}/` | Learner-safe single lecture. Video → HLS playlist + renditions; article → article text. Returns the caller's `progress` to support player resume. |
| POST | `/api/v1/courses/learn/lectures/{lecture_id}/progress/` | Idempotent upsert of `WatchProgress` (`watched_seconds`, `is_completed`). `watched_seconds` is server-clamped to the active video's `duration_seconds`; if the clamped cursor lands at duration, `is_completed` is forced to `True`. Articles force `watched_seconds=0`. Debounced enrollment-touch via `update_last_accessed`. Triggers `progress_percent` recalc via the `WatchProgress` `post_save` signal. |
| GET | `/api/v1/courses/learn/quizzes/{quiz_id}/` | Quiz + questions + answer options (no `is_correct`) for the attempt UI. Returns the caller's `latest_attempt` summary if one exists. |
| POST | `/api/v1/courses/learn/quizzes/{quiz_id}/submit/` | Submit selected answers; creates a new `QuizAttempt` + `QuizAttemptAnswer` rows. Returns per-question verdict; `correct_answer_*` fields appear only when the answer was wrong. Calls `recalculate_progress` at the end of the transaction so quiz attempts roll into `progress_percent`. |
| GET | `/api/v1/courses/learn/assignments/{assignment_id}/` | Assignment + questions for the attempt UI. `model_answer` and `rubric` are never declared on the learner serializer. Returns the caller's `latest_submission` summary if one exists. |
| POST | `/api/v1/courses/learn/assignments/{assignment_id}/submit/` | Creates an `AssignmentSubmission(status='submitted')` + one `AssignmentSubmissionAnswer` per question (`rubric_snapshot` + `max_score` frozen at submit time). Returns `202 Accepted`; the Celery task `grade_assignment_submission_task` runs the `RubricGrader` and transitions the row to `passed` / `failed` / `grading_failed`. `422` if an in-flight submission already exists. |
| GET | `/api/v1/courses/learn/assignments/submissions/{submission_id}/` | Per-question score + `criterion_results` + `feedback`; `model_answer` revealed only when `status in (passed, failed)`. The polling target for the frontend. |
| POST | `/api/v1/courses/learn/assignments/submissions/{submission_id}/retry/` | Re-enqueue grading for a `grading_failed` submission. Reuses the same row so `submitted_at` and historical correlation stay correct. `422` for any non-`grading_failed` status; `404` for another learner's submission. |
| GET | `/api/v1/courses/learn/coding-exercises/{exercise_id}/` | Coding-exercise detail with starter code + visible test cases + latest submission summary. `solution_code` and hidden test cases are never present. |
| POST | `/api/v1/courses/learn/coding-exercises/{exercise_id}/run/` | Transient Run: visible test cases only, no DB row. Returns `202 + {task_id}`. Result lives in Celery result backend with `CELERY_RESULT_EXPIRES = 3600` TTL. |
| GET | `/api/v1/courses/learn/coding-exercises/tasks/{task_id}/` | Poll the Run task. States: `PENDING` / `STARTED` / `SUCCESS` (with `result` dict) / `FAILURE`. |
| POST | `/api/v1/courses/learn/coding-exercises/{exercise_id}/submit/` | Persisted Submit: creates a `CodingSubmission(status='queued')` snapshotting `total_tests`, then dispatches `evaluate_coding_submission_task` via `transaction.on_commit`. Returns `202`. |
| GET | `/api/v1/courses/learn/coding-exercises/submissions/{submission_id}/` | Polling target. Hidden test rows are omitted entirely from `test_results`; aggregate counts (`total_tests` / `passed_tests` / `score`) still include them. |
| POST | `/api/v1/courses/learn/coding-exercises/submissions/{submission_id}/retry/` | Re-enqueue evaluation for a submission stuck in `error`. Reuses the row so `submitted_at` is preserved. Only `error` is retryable (use `/submit/` for fresh attempts after `failed`/`passed`). |

Assignment manual-override / instructor moderation surface is parked for Phase 3. The coding-exercise execution pipeline + sandbox model is documented in [`docs/submission-flow.md`](../submission-flow.md); see also [`docs/architecture/09-coding-exercises.md`](./09-coding-exercises.md) Part 2.

## Enrollment Flow

1. **Browse** — Anyone visits `/catalog/` and `/catalog/{slug}/` to discover courses.
2. **Enroll** — Authenticated learner with verified email calls `POST /{slug}/enroll/`.
3. **Access** — Enrolled learner accesses course content via the Phase-1 learner endpoints: `/learn/{slug}/curriculum/` for the sidebar, `/learn/lectures/{id}/` for a single playable lecture, `/learn/lectures/{id}/progress/` to record watch progress. The course-player page header is served by `/my-courses/{slug}/`.
4. **Progress** — As the learner completes lectures (via `WatchProgress`), the enrollment service recalculates `progress_percent`.
5. **Complete** — When `progress_percent` reaches 100, `completed_at` is set.
6. **Unenroll** — Learner can soft-deactivate via `POST /{slug}/unenroll/`. Progress is preserved for re-enrollment.

## Re-enrollment

If a learner unenrolls and later re-enrolls, the existing `Enrollment` record is reactivated (not duplicated). Their prior `progress_percent` and `WatchProgress` records remain intact.

## Progress Calculation

```
progress_percent = min(int((completed_content_items / total_content_items) * 100), 100)
```

**Completion rules per content type:**

| Content type | Counted as complete when | Trigger |
|---|---|---|
| Lecture (video/article) | `WatchProgress.is_completed = True` | `WatchProgress` `post_save` signal (fires only when `is_completed` changes) |
| Quiz | ≥1 `QuizAttempt` exists for `(user, quiz)` (any score) | `submit_quiz_attempt()` calls `recalculate_progress()` directly at end of transaction |
| Assignment | `AssignmentSubmission.status = 'passed'` | `grade_assignment_submission_task` schedules via `transaction.on_commit` only when verdict is `passed` |
| Coding exercise | `CodingSubmission.status = 'passed'` — **distinct per exercise** (multiple passed attempts count once) | `evaluate_coding_submission_task` schedules via `transaction.on_commit` only when verdict is `passed` |

**`recalculate_progress(enrollment)`** in `courses/services/enrollment_service.py` is the single
entry point. It avoids N+1 by:
1. Querying all `SectionContent` rows for the course in one query
2. Grouping them by `item_type` → sets of object IDs
3. For each type, running one query to find the completed subset
4. Computing score from the intersection counts

**Side effects:**
- If `progress_percent == 100` and `enrollment.completed_at is None` → sets `completed_at = now()`
- If `progress_percent < 100` and `enrollment.completed_at is not None` → clears `completed_at`
  (handles edge case where an instructor removes content after a learner has "completed" the course)

A recalculation failure can't roll back a valid pass verdict because the rollup runs after commit
(`transaction.on_commit`).

## Permissions

| Permission Class | Location | Purpose |
|-----------------|----------|---------|
| `IsLearnerUser` | `core/permissions.py` | Gates enrollment-write endpoints (`/{slug}/enroll/`, `/{slug}/unenroll/`, `/learn/lectures/{id}/progress/`) to learner accounts only. |

Catalog endpoints use `AllowAny` (public). My-courses + Phase-1 read endpoints accept *enrolled learner OR the course's own instructor* (`IsLearnerUser | IsInstructorUser`).

Access-denied status codes follow the project-wide rule (see CLAUDE.md): slug-based URLs return **403** (slugs are public via catalog so existence is not a secret); numeric-ID URLs return **404** (don't help attackers enumerate IDs). Applied here: `/my-courses/{slug}/` and `/learn/{slug}/curriculum/` return 403 for unenrolled non-instructors; `/learn/lectures/{id}/` and `/learn/lectures/{id}/progress/` return 404.

## Design Decisions

**Why denormalize `progress_percent`?** The "My Courses" dashboard shows progress for every enrolled course. Computing it on-the-fly would require JOINs across `Enrollment → NidusCourse → CourseSection → SectionContent → WatchProgress` for each course. A denormalized field makes the dashboard query a simple `SELECT` on the `enrollments` table.

**Why soft-delete on unenroll?** Preserving the enrollment record (with `is_active=False`) retains the learner's progress and allows seamless re-enrollment without data loss. The unique constraint on `(user, course)` covers both active and inactive enrollments.

**Why no payment integration yet?** The `enrollment_type` field future-proofs the schema. Until payment integration is added, all enrollments are created with `enrollment_type='free'`, even if a course has a non-zero `price`. When a `payments` app is built, it will create enrollments with `enrollment_type='paid'` upon successful checkout. The enrollment system itself doesn't need to change.

## Files Created / Modified

| File | Change |
|------|--------|
| `courses/all_models/enrollment_models.py` | New — `Enrollment` model |
| `courses/all_models/__init__.py` | Added `enrollment_models` import |
| `courses/services/enrollment_service.py` | New — enroll, unenroll, progress, catalog queries |
| `courses/services/__init__.py` | Added enrollment service re-exports |
| `courses/all_serializers/enrollment_serializers.py` | New — catalog + enrollment serializers |
| `courses/all_serializers/__init__.py` | Added `enrollment_serializers` import |
| `courses/all_views/enrollment_views.py` | New — catalog, enroll, my-courses views |
| `courses/all_views/__init__.py` | Added enrollment view re-exports |
| `courses/views.py` | Added enrollment view re-exports |
| `courses/urls.py` | Added 6 new URL patterns |
| `core/permissions.py` | Added `IsLearnerUser` permission class |

## Roadmap

### Done — Phase 1 consumption (split-endpoint design)

The "learner curriculum view", "learner lecture view", and "watch progress endpoint" originally listed here as future work have all been implemented under the `/learn/...` prefix rather than nested under `/my-courses/`. The URL choice keeps the consumption surface separate from the dashboard surface and makes per-item endpoints easier to extend in Phase 2. The previously monolithic `/my-courses/{slug}/` (which returned the full course tree) was slimmed to header metadata only at the same time. See `MY_COURSES_PERFORMANCE_AUDIT.md` for the pre/post-refactor query-count and payload-size comparison.

### Done — Phase 2 consumption (assignment + coding)

- **Assignment auto-grading** — `AssignmentSubmission` + `AssignmentSubmissionAnswer` models, `AssignmentQuestion.rubric` field, `RubricGrader`, `grade_assignment_submission_task`, and the four `/learn/assignments/...` endpoints. Full rationale in `LEARNER_ASSIGNMENT_CONSUMPTION_DESIGN.md`.
- **Coding-exercise execution** — `CodingSubmission` + `CodingSubmissionTestResult` models, Docker sandbox runner (`courses/services/code_runner.py`, one container per submission), per-language batched harness, `evaluate_coding_run_task` + `evaluate_coding_submission_task` + `reap_stuck_coding_submissions_task` (Celery beat), and six `/learn/coding-exercises/...` endpoints. Full pipeline doc: [`docs/submission-flow.md`](../submission-flow.md). Optimisation rationale (one container per submission vs per test case): [`docs/comparison.md`](../comparison.md) §17. Implementation overview: [`docs/architecture/09-coding-exercises.md`](./09-coding-exercises.md) Part 2.

### Not yet built

- **Assignment manual override / moderation surface** — instructor-side endpoints to view, override, or comment on submissions. Phase-3 addition; not required for the auto-grading-only v1.
- **Coding sandbox hardening** — Docker-out-of-Docker is demo-only. Replace with gVisor / Firecracker / remote workers for untrusted execution. See [`docs/comparison.md`](../comparison.md) §17 for migration paths.
- **Caching on the consumption surface** — Redis or HTTP cache for the slim `/my-courses/{slug}/` and `/learn/{slug}/curriculum/` responses. Lower priority now that the response is small; revisit if traffic warrants.
- **Payment integration** — A `payments` app that creates `enrollment_type='paid'` enrollments on checkout.
- **Certificates** — PDF generation triggered when `completed_at` is set.
- **Course reviews/ratings** — Learner feedback on completed courses.
