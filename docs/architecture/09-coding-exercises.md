# 09) Coding Exercises

Coding exercises are interactive programming problems instructors author as curriculum items. This document is the **authoritative** reference and covers both **Part 1 (instructor authoring CRUD)** and **Part 2 (learner execution: Run / Submit + Docker sandbox)** — the end-to-end pipeline, sandbox limits, harness contract, failure modes, and the rationale for the **one container per submission** optimisation.

## Run vs. Submit — at a glance

```
                    RUN                          SUBMIT
                ─────────────────────────────────────────────
Endpoint        POST /learn/coding-exercises      POST /learn/coding-exercises
                      /{id}/run/                        /{id}/submit/
Test cases      Visible only (is_hidden=False)   All (visible + hidden)
DB row          No — result in Celery backend     Yes — CodingSubmission row
                (1-hour TTL)
Returns         202 + { task_id }                202 + queued CodingSubmission
Poll via        GET /learn/coding-exercises/      GET /learn/coding-exercises/
                      /tasks/{task_id}/                  /submissions/{id}/
Learner intent  "Try it" / IDE feedback           Graded attempt
Retryable?      No (just re-run)                  Yes — /retry/ if status=error
Progress        Never                             Yes — on status=passed
─────────────────────────────────────────────────────────────

Status flow (Submit):
  queued ──► grading ──► passed
                    └──► failed
                    └──► error  (retryable via /retry/)

Status precedence: error > failed > passed
  (if any test errors → error; else if any fail → failed; else passed)
```

## Key files

### Part 1 — Authoring (instructor)
- `courses/all_models/assessment_models.py`: `CodingExercise`, `CodingExerciseLanguageConfig`, `CodingTestCase`
- `courses/all_views/coding_views.py`: instructor authoring endpoints
- `courses/all_serializers/assessment_serializers.py`: `CodingExerciseSerializer`, `CodingExerciseCreateUpdateSerializer`, `CodingExerciseLanguageConfigSerializer`, `CodingTestCaseSerializer`
- `courses/urls.py`: route definitions
- `core/permissions.py`: `IsInstructorUser` (gates all authoring endpoints; identity verification not required — see [08-core-infrastructure.md](08-core-infrastructure.md))

### Part 2 — Learner execution
- `courses/all_models/assessment_models.py`: `CodingSubmission`, `CodingSubmissionTestResult`
- `courses/all_views/learner_views.py`: `LearnerCodingExerciseDetailView`, `LearnerCodingRunView`, `LearnerCodingSubmitView`, `LearnerCodingTaskStatusView`, `LearnerCodingSubmissionDetailView`, `LearnerCodingSubmissionRetryView`
- `courses/all_serializers/learner_serializers.py`: `LearnerCodingExerciseDetailSerializer`, `LearnerCodingSubmissionSerializer`, `_LearnerCodingSubmissionTestResultSerializer` (redaction), `CodingRunSubmitSerializer`
- `courses/services/learner_service.py`: `get_coding_exercise_for_consumption`, `run_coding_exercise`, `submit_coding_exercise`, `get_learner_coding_submission`, `retry_coding_submission`, `CodingSubmissionError`
- `courses/services/code_runner.py`: `CodeRunner`, `SingleTestResult`, `DockerTransientError`, `DockerUnavailableError`, per-language harnesses, `_normalize`, sentinel parser
- `courses/tasks.py`: `evaluate_coding_run_task`, `evaluate_coding_submission_task`, `reap_stuck_coding_submissions_task`
- `core/permissions.py`: `IsLearnerUser` (gates Run/Submit/retry POST endpoints)

## Models and fields

### `CodingExercise`

The top-level problem object attached to a section.

- `section` (FK -> `CourseSection`)
- `title` — display name shown in curriculum
- `description` — short summary of the exercise
- `problem_statement` — full problem description (markdown/rich text)
- `difficulty` — `easy | medium | hard`
- `default_language` — the language pre-selected when a learner opens the exercise
- `supported_languages` — JSON list of language codes the exercise supports
- `time_limit_ms` — optional time limit in milliseconds for execution
- DB indices on `(section, difficulty)` for curriculum filtering

Has a `GenericRelation` to `SectionContent` — deleting the exercise cascades and removes its `SectionContent` slot automatically.

### `CodingExerciseLanguageConfig`

Per-language code templates attached to an exercise.

- `exercise` (FK -> `CodingExercise`)
- `language` — `python | javascript | cpp | java`
- `starter_code` — the boilerplate shown to learners when they open the exercise
- `solution_code` — the reference implementation used for grading/instructor reference

**Security rule**: `solution_code` must never appear in any learner-facing serializer or response. It is included in `CodingExerciseLanguageConfigSerializer` only because all coding endpoints require `IsInstructorUser` (`user_type == 'instructor'`, not learner) plus the `instructors=request.user` ownership filter.

- Unique constraint: `(exercise, language)` — one config per language per exercise.

### `CodingTestCase`

Ordered input/output test cases for an exercise.

- `exercise` (FK -> `CodingExercise`)
- `input_data` — the stdin or input structure fed to the solution
- `expected_output` — the output the solution must produce to pass
- `is_hidden` — if `True`, this test case is used only for grading and is never returned to learners
- `explanation` — optional note shown to learners when they fail a visible test case
- `position` — ordering of test cases within the exercise (1-based, contiguous)
- Unique constraint: `(exercise, position)` — enforces ordered, gap-free positions

## API endpoints

All endpoints require `IsAuthenticated`, `IsEmailVerified`, `IsInstructorUser` (identity verification not required to author — see [08-core-infrastructure.md](08-core-infrastructure.md)).
Ownership is enforced via `section__course__instructors=request.user` in every queryset filter.

### Exercise detail

```
GET    /coding-exercises/{exercise_id}/       → retrieve exercise with nested configs and test cases
PATCH  /coding-exercises/{exercise_id}/       → update exercise metadata (partial)
DELETE /coding-exercises/{exercise_id}/       → delete exercise (cascades SectionContent)
```

Exercise creation is handled through the unified curriculum endpoint:
```
POST /sections/{section_id}/contents/  { "item_type": "coding", ... }
```

### Language configs

```
GET    /coding-exercises/{exercise_id}/language-configs/                    → list all configs
POST   /coding-exercises/{exercise_id}/language-configs/                    → add a language config
GET    /coding-exercises/{exercise_id}/language-configs/{config_id}/        → retrieve one config
PATCH  /coding-exercises/{exercise_id}/language-configs/{config_id}/        → update config (partial)
DELETE /coding-exercises/{exercise_id}/language-configs/{config_id}/        → delete config
```

Posting a config for a language that already exists on the exercise returns `400` (IntegrityError caught in view).

### Test cases

```
GET    /coding-exercises/{exercise_id}/testcases/              → list all test cases (ordered by position, then id)
POST   /coding-exercises/{exercise_id}/testcases/              → create a test case
GET    /coding-exercises/{exercise_id}/testcases/{tc_id}/      → retrieve one test case
PATCH  /coding-exercises/{exercise_id}/testcases/{tc_id}/      → update test case (partial)
DELETE /coding-exercises/{exercise_id}/testcases/{tc_id}/      → delete test case (auto-shifts positions)
```

## Test case position management

Test case positions are 1-based and must remain contiguous (1, 2, 3 … n) with no gaps.

**On create**: the caller supplies `position`. If a test case already exists at that position, a `400` is returned (IntegrityError).

**On delete**: `CodingTestCaseDetailAPIView.delete` wraps the operation in `transaction.atomic()`:
1. Records `deleted_position` and `exercise_id` before deletion.
2. Deletes the test case row.
3. Issues a single bulk `UPDATE ... SET position = position - 1 WHERE position > deleted_position` using `F('position') - 1`.

This keeps the sequence contiguous and avoids N individual saves.

## Authoring process (step-by-step)

1. **Create the exercise** via the unified contents endpoint:
   ```
   POST /sections/{section_id}/contents/
   { "item_type": "coding", "title": "Two Sum", "difficulty": "easy", ... }
   ```
   Returns a `SectionContent` object; the `object_id` field is the `exercise_id`.

2. **Add language configurations** — one per supported language:
   ```
   POST /coding-exercises/{exercise_id}/language-configs/
   { "language": "python", "starter_code": "def two_sum(...):", "solution_code": "..." }
   ```

3. **Add test cases** in order:
   ```
   POST /coding-exercises/{exercise_id}/testcases/
   { "input_data": "[2,7,11,15]\n9", "expected_output": "[0,1]", "position": 1, "is_hidden": false }
   ```
   Hidden test cases (`is_hidden: true`) are for final grading and are never returned to learners.

4. **Update exercise details** as needed:
   ```
   PATCH /coding-exercises/{exercise_id}/
   { "problem_statement": "Given an array of integers..." }
   ```

5. **Reorder** the exercise in the section curriculum the same way as any other item:
   ```
   PATCH /contents/{content_id}/reorder/
   { "position": 3 }
   ```

## Serializer notes

| Serializer | Used for | Notes |
|---|---|---|
| `CodingExerciseSerializer` | Read (GET) | Nests `language_configs` and `test_cases`; instructor-only |
| `CodingExerciseCreateUpdateSerializer` | Write (PATCH via detail view) | Validates `supported_languages` list |
| `CodingExerciseLanguageConfigSerializer` | Read and write on configs | Includes `solution_code`; must never be used in learner APIs |
| `CodingTestCaseSerializer` | Read and write on test cases | `is_hidden` field is present; learner-facing views must filter or exclude hidden cases |

## System Explanation (Why This Design)

**Why are language configs a separate model instead of a JSON field on `CodingExercise`?**
A separate table with a unique `(exercise, language)` constraint gives per-language CRUD, clean DB indexing, and a clear place to attach execution metadata in the future (e.g., Docker image tag, memory limit per language).

**Why is `solution_code` on the same model as `starter_code`?**
Keeping them together makes it impossible to accidentally return `starter_code` without also managing `solution_code` visibility — both fields live on `CodingExerciseLanguageConfig`, which is only reachable through instructor-gated endpoints.

**Why does test case deletion shift positions atomically?**
Contiguous positions simplify client-side rendering — the UI can treat `position` as an array index. A single `UPDATE` with `F('position') - 1` is more efficient than N individual saves and avoids partial-shift states if the request fails mid-way.

**Why is exercise creation routed through the unified contents endpoint instead of a dedicated `POST /coding-exercises/` endpoint?**
The unified `POST /sections/{section_id}/contents/` endpoint creates both the domain object and its `SectionContent` slot in one transaction. A separate creation endpoint would require the caller to make two requests (create exercise, then create SectionContent), which creates an opportunity for orphaned exercises with no curriculum placement.

---

# Part 2 — Learner Execution (Run / Submit)

The execution surface adds two new persisted models and six new learner-facing endpoints, integrated into the Career-College codebase as described below.

## Two execution modes

| Mode | Endpoint | Persisted? | Test cases run | Returns | Poll via |
|---|---|---|---|---|---|
| **Run** | `POST /learn/coding-exercises/{id}/run/` | No — Celery result only (TTL = `CELERY_RESULT_EXPIRES`) | Visible only (`is_hidden=False`) | `{task_id}` (HTTP 202) | `GET /learn/coding-exercises/tasks/{task_id}/` |
| **Submit** | `POST /learn/coding-exercises/{id}/submit/` | Yes — `CodingSubmission` row | All (visible + hidden) | Queued submission (HTTP 202) | `GET /learn/coding-exercises/submissions/{id}/` |

Run is the IDE "try it" button — cheap, ephemeral, hides nothing the learner could not already see. Submit is the graded attempt; every test case (including hidden) is evaluated and the row is the permanent record used for `is_solved` and `progress_percent` calculations.

## Models

### `CodingSubmission`

| Field | Type | Notes |
|---|---|---|
| `user` | FK User | Learner who submitted |
| `exercise` | FK CodingExercise | |
| `language` | CharField(20) | One of `python` / `javascript` / `cpp` / `java` |
| `code` | TextField | Verbatim learner code |
| `status` | CharField | `queued` / `grading` / `passed` / `failed` / `error` |
| `total_tests` | PositiveInt | Snapshotted at submit time (instructor edits later don't change it) |
| `passed_tests` | PositiveInt | Computed by the task |
| `score` | Decimal(5,2) | `round(passed_tests/total_tests*100, 2)` |
| `runtime_ms` | PositiveInt | Sum of per-test runtimes |
| `error_message`, `stdout`, `stderr` | TextField | Capped at 5000 chars each |
| `submitted_at`, `completed_at` | DateTime | `completed_at` is null until terminal |

`TERMINAL_STATUSES = (PASSED, FAILED, ERROR)`, `IN_FLIGHT_STATUSES = (QUEUED, GRADING)` — used by the idempotency check inside the task and the in-flight guard in `submit_coding_exercise`.

### `CodingSubmissionTestResult`

One row per executed test case. `test_case` is `on_delete=SET_NULL` so result rows survive instructor-side test-case deletion. `input_data` / `expected_output` / `actual_output` are **snapshotted** at write time — historical rows stay correct even if the underlying `CodingTestCase` is later edited. `is_hidden` is **copied** onto the row at write time so the redaction layer never re-reads the test case.

## Endpoints

| Method | Endpoint | Permission | Purpose |
|---|---|---|---|
| GET | `/learn/coding-exercises/{exercise_id}/` | `IsLearnerUser \| IsInstructorUser` | Detail: starter code + visible test cases + latest submission summary. `solution_code` is **not declared** on the serializer (absence > conditional strip). Hidden test cases filtered out by the service before reaching the serializer. |
| POST | `/learn/coding-exercises/{exercise_id}/run/` | `IsLearnerUser` | Dispatch transient evaluation. Returns `202 + {task_id}`. No DB row. |
| GET | `/learn/coding-exercises/tasks/{task_id}/` | `IsEmailVerified` | Poll Celery `AsyncResult`. States: `PENDING` / `STARTED` / `SUCCESS` (with `result` dict) / `FAILURE`. |
| POST | `/learn/coding-exercises/{exercise_id}/submit/` | `IsLearnerUser` | Create `CodingSubmission(status='queued')` and dispatch the task on commit. Returns `202` with the queued row. |
| GET | `/learn/coding-exercises/submissions/{submission_id}/` | `IsLearnerUser` (owner only) | Poll target. Hidden test rows are omitted entirely from `test_results`; aggregates still include them. |
| POST | `/learn/coding-exercises/submissions/{submission_id}/retry/` | `IsLearnerUser` (owner only) | Re-enqueue evaluation for a submission stuck in `error`. Only `error` is retryable (use `/submit/` for fresh attempts after `failed`/`passed`). |

403-vs-404 policy: all endpoints take numeric IDs → 404 for unauthorised callers (don't leak existence). Slug-based routes don't exist on this surface.

## Sandbox & runner contract

The Docker sandbox + per-language harness lives in `courses/services/code_runner.py`. Details:

- **One container per submission**, not per test case. Each language's harness loops over `INPUT_0..INPUT_{N-1}` env vars internally with per-test try/except, emitting sentinel-delimited per-test results on stdout. This eliminates `(N-1)` container startups + `(N-1)` compiles for C++/Java — the rationale for the one-container model.
- **Sandbox constants**: `runtime='runsc'` (gVisor; falls back to `runc` via `RUNNER_RUNTIME` for local dev), `network_disabled=True`, `mem_limit=128m`, `memswap_limit=128m`, `nano_cpus=500_000_000` (0.5 cores), `pids_limit=64` (fork-bomb cap), `ulimits=[fsize=10MB, nproc=64, nofile=128, cpu=10s]`, `read_only=True`, `tmpfs={'/tmp': 'size=32m,exec'}`, `cap_drop=['ALL']`, `security_opt=['no-new-privileges:true']`, `detach=True, remove=False` (manual cleanup in `finally`). Wall-clock budget enforced via `container.wait(timeout=...)` + `container.kill()` on overshoot.
- **gVisor (`runsc`) runtime**: provides user-space syscall interception so a kernel exploit inside the learner container cannot directly pivot to the host kernel. Required on multi-tenant or shared hosts; install instructions in [`README.md` → Development Setup → Step 10](../../README.md#10-install-gvisor-runsc-on-production--shared-hosts). Note: does **not** mitigate the Docker-out-of-Docker daemon-socket risk — that requires moving the runner to a dedicated host or switching to Kata Containers / Firecracker.
- **Learner-code contract**: a top-level `solve(input_string)` function (Python / JavaScript) or `void solve(const std::string&)` (C++) / `static void solve(String)` (Java) — one string parameter; return the answer or write it to stdout. Multi-argument parsing is the learner's job.
- **C++ harness explicitly avoids `<bits/stdc++.h>`** — pulling it under `-O2` exceeds the 128 MB memory cap and OOM-kills `cc1plus`. The prologue includes only the headers the harness needs (`<iostream>`, `<sstream>`, `<string>`, `<chrono>`, `<cstdlib>`, `<stdexcept>`); learners are free to add more.
- **Image overrides**: `RUNNER_IMAGE_PYTHON` / `RUNNER_IMAGE_JAVASCRIPT` / `RUNNER_IMAGE_CPP` / `RUNNER_IMAGE_JAVA`. Defaults from Docker Hub (rate-limited for unauthenticated pulls — pre-pull or override in production).
- **WARNING (echoed from CLAUDE.md)**: Docker-out-of-Docker — the daemon socket is shared with the host; a sufficiently advanced attacker can escape. Demo / single-tenant use only.

## Tasks

| Task | Decoration | Purpose |
|---|---|---|
| `evaluate_coding_run_task` | `@shared_task` (no retries) | Loads exercise + visible test cases, calls `CodeRunner`, returns a plain dict (no DB write). Run-mode infrastructure failures are reported inside the result dict — they do NOT raise into Celery `FAILURE`. |
| `evaluate_coding_submission_task` | `bind=True, acks_late=True, autoretry_for=(DockerTransientError,), max_retries=3` | Mirrors `grade_assignment_submission_task`. Early-returns on terminal status (idempotent under `acks_late` redelivery). Atomic block: bulk-insert `CodingSubmissionTestResult` rows + update aggregates. On `status=passed` schedules `recalculate_progress` via `transaction.on_commit`. On final retry exhaustion: `_finalize_with_error` flips status to ERROR. |
| `reap_stuck_coding_submissions_task` | `@shared_task` (Celery beat, 60 s) | Flips `CodingSubmission` rows whose status is `queued`/`grading` for more than 5 min to `error` with `error_message='Reaped: worker crashed or runner stalled.'`. Prevents polling UIs from hanging on dead workers. |

Status precedence on a Submit verdict is **`error > failed > passed`**: if any test errors, the submission is `error`; else if any test fails, `failed`; else `passed`. Implemented in the aggregate block of `evaluate_coding_submission_task`.

## Redaction layers (defence-in-depth)

Identical principle to assignments — three layers, each independent:

1. **Task layer**: `evaluate_coding_run_task` queries `test_cases.filter(is_hidden=False)`. Hidden cases never enter Run pipeline at all.
2. **Persistence layer**: `evaluate_coding_submission_task` executes every test, but copies `tc.is_hidden` onto each `CodingSubmissionTestResult` row. The redaction layer therefore never re-reads the underlying test case (which may have changed or been deleted).
3. **Serializer layer**: `LearnerCodingSubmissionSerializer.get_test_results` filters `is_hidden=False` before serialising. Hidden rows are **omitted entirely** from `test_results`. Aggregate fields (`total_tests`, `passed_tests`, `score`, `runtime_ms`) still cover all tests, so the learner sees overall pass/fail without per-row data.

`solution_code` is never declared on `_LearnerCodingLanguageConfigSerializer` — the field is structurally absent, not conditionally stripped.

## Progress integration

`recalculate_progress` in `courses/services/enrollment_service.py` now counts distinct PASSED coding exercises:

```python
completed_coding_ids = set(
    CodingSubmission.objects.filter(
        user=enrollment.user,
        exercise_id__in=coding_ids,
        status=CodingSubmission.Status.PASSED,
    ).values_list('exercise_id', flat=True)
)
```

Distinct per exercise — multiple PASSED attempts on the same exercise don't double-count. Triggered from the task via `transaction.on_commit` only when the final status is `PASSED` (a recalc failure can't roll back a valid pass verdict).

## Idempotency / retry / failure model

- **acks_late + early-return on terminal**: if the broker redelivers a message because the worker died after `prefetch` but before `ack`, the next invocation sees `status in TERMINAL_STATUSES` and short-circuits without re-grading.
- **`autoretry_for=(DockerTransientError,)`**: only retried for transient infrastructure errors (connection / timeout). `ImageNotFound` and learner-code errors are NOT retried — they're terminal verdicts that shouldn't burn worker capacity.
- **`DockerUnavailableError` is NOT retried**: daemon-down is operator action, not transient. The submission goes straight to `error`.
- **Retry endpoint**: `POST /learn/coding-exercises/submissions/{id}/retry/` resets status to `queued`, clears `error_message`, redispatches the task on commit. Only `error` is retryable.
- **Zombie reaper**: Celery beat task catches anything still in_flight after 5 min. Prevents UI hangs when the worker crashes and broker redelivery is unavailable.

## Testing

CLAUDE.md is explicit: tests must **never** hit real Docker. Patch `courses.services.code_runner.CodeRunner.run_submission` to return deterministic `SingleTestResult` lists; Celery is in eager mode so `.delay()` runs synchronously. Coverage lives in `courses/all_tests/test_learner_coding_consumption.py` — 27 tests including:

- Detail visibility (no `solution_code`, hidden tests filtered, 404 for unenrolled, instructor preview)
- Run dispatch with visible-tests-only filtering, 403 for instructor, 400 for unsupported language
- Submit persistence with full test set, hidden row redaction, status precedence (`error > failed > passed`), in-flight 422, owner-only 404
- Retry: only `error` is retryable, 404 for other learners
- Reaper: stale rows flipped, fresh rows untouched
- Progress: PASSED triggers recalc, FAILED doesn't
- Idempotency under acks_late redelivery (`evaluate_coding_submission_task.run` short-circuits on terminal)

End-to-end smoke verification against real Docker lives in `scripts/smoke_code_runner.py` (all 4 languages) and `scripts/smoke_runtime_error.py` (per-test try/except isolation). Manual — not in `manage.py test`.

## System Explanation (Why This Design)

**Why one container per submission instead of one per test case?** A naive `1 test = 1 container` model means, for C++ and Java, N compile cycles per submission plus N × ~300 ms container startup overhead. Batching all tests into one container is the highest-leverage optimisation, so we apply it here. Sandbox isolation is preserved (the container still has the full 128 MB / 0.5 CPU / no-network / read-only contract); only the batching changes.

**Why omit hidden test rows entirely instead of redacting fields?** Earlier design kept the row visible with input/expected/actual blanked and status visible. User-requested change: strip rows entirely so the learner can't even see hidden-test row count. Aggregate counts still leak existence (learner can compute `total_tests - len(visible_results)` = hidden count) but no per-test signal. Cheaper to audit too — one `filter(is_hidden=False)` in the serializer vs a conditional `to_representation` branch.

**Why retry only `error`, not `failed`?** `failed` is a correct verdict — the learner's code didn't pass the tests. Retrying it without new code would just produce the same `failed` again. `error` is the only status where the verdict might not reflect the code (transient Docker hiccup, network blip while pulling, etc.), so retry is meaningful.

**Why isn't Run persisted?** Run is the IDE "try it" button — it'd produce one row per keystroke-cycle learner saves. The Celery result backend with a 1-hour TTL is perfect for this: lives long enough to render in the UI, expires before it pollutes anything. The Submit path exists specifically to provide the persistent record.
