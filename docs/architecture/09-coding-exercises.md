# 09) Coding Exercises

Coding exercises are interactive programming problems instructors author as curriculum items. This document is the **authoritative** reference and covers both **Part 1 (instructor authoring CRUD)** and **Part 2 (learner execution: Run / Submit + Docker sandbox)**.

**Grading model: script evaluation (Udemy-style).** Each exercise targets **exactly one language** and carries its authoring bundle directly on the model: `starter_code`, `solution_code`, and an **evaluation script** (`CodingExercise.evaluation_script`) — a test file that imports/calls the learner's code and asserts on it. Grading runs that script against the learner's code in one sandboxed container; each test in the script produces one named result (`passed` / `failed` / `error` + failure message). There are **no I/O test-case pairs and no expected-output string comparison** — `CodingTestCase` was removed in migration `0023_script_only_evaluation`, and the per-language `CodingExerciseLanguageConfig` model (plus `problem_statement`, `difficulty`, `default_language`, `supported_languages`) was flattened away in `0024_single_language_coding_exercise`.

## Run vs. Submit — at a glance

```
                    RUN                          SUBMIT
                ─────────────────────────────────────────────
Endpoint        POST /learn/coding-exercises      POST /learn/coding-exercises
                      /{id}/run/                        /{id}/submit/
Suite           Full evaluation script            Full evaluation script
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
- `courses/all_models/assessment_models.py`: `CodingExercise`
- `courses/all_views/coding_views.py`: instructor authoring endpoints
- `courses/all_serializers/assessment_serializers.py`: `CodingExerciseSerializer`, `CodingExerciseCreateUpdateSerializer`
- `courses/urls.py`: route definitions
- `core/permissions.py`: `IsInstructorUser` (gates all authoring endpoints; identity verification not required — see [08-core-infrastructure.md](08-core-infrastructure.md))

### Part 2 — Learner execution
- `courses/all_models/assessment_models.py`: `CodingSubmission`, `CodingSubmissionTestResult`
- `courses/all_views/learner_views.py`: `LearnerCodingExerciseDetailView`, `LearnerCodingRunView`, `LearnerCodingSubmitView`, `LearnerCodingTaskStatusView`, `LearnerCodingSubmissionDetailView`, `LearnerCodingSubmissionRetryView`
- `courses/all_serializers/learner_serializers.py`: `LearnerCodingExerciseDetailSerializer`, `LearnerCodingSubmissionSerializer`, `_LearnerCodingSubmissionTestResultSerializer`, `CodingRunSubmitSerializer`
- `courses/services/learner_service.py`: `get_coding_exercise_for_consumption`, `run_coding_exercise`, `submit_coding_exercise`, `get_learner_coding_submission`, `retry_coding_submission`, `_validate_evaluation_script`, `CodingSubmissionError`
- `courses/services/code_runner.py`: `CodeRunner`, `ScriptTestResult`, `DockerTransientError`, `DockerUnavailableError`, per-language micro-harnesses, `_parse_script_output`
- `courses/tasks.py`: `evaluate_coding_run_task`, `evaluate_coding_submission_task`, `reap_stuck_coding_submissions_task`
- `core/permissions.py`: `IsLearnerUser` (gates Run/Submit/retry POST endpoints)

## Models and fields

### `CodingExercise`

The one and only coding model on the authoring side — problem, language, starter code, solution, and evaluation script all live here.

- `section` (FK -> `CourseSection`)
- `title` — display name shown in curriculum
- `description` — the problem text shown to learners. **Must state the function contract** the learner has to implement (names + signatures), since the evaluation script imports and calls those functions directly.
- `language` — `python | javascript | cpp | java` (single language per exercise)
- `starter_code` — the boilerplate shown to learners. In script evaluation the starter code **is the contract**: it declares the function signature(s) the evaluation script will call.
- `solution_code` — the reference implementation (instructor reference)
- `evaluation_script` — the instructor's test file. **This is the grading source of truth**: it imports the learner's code and asserts on it. Run/Submit are refused (422) while it is blank, and a course cannot leave `draft` while any exercise misses one (`_validate_course_completeness`).
- `time_limit_ms` — wall-clock budget for the **whole evaluation suite** (the script decides the test count, so per-test limits can't be known upfront)

Has a `GenericRelation` to `SectionContent` — deleting the exercise cascades and removes its `SectionContent` slot automatically.

**Security rule**: `solution_code` and `evaluation_script` must never appear in any learner-facing serializer or response — the evaluation script literally contains the expected answers. Both are included in the instructor-only `CodingExerciseSerializer` because all coding authoring endpoints require `IsInstructorUser` plus the `instructors=request.user` ownership filter. `LearnerCodingExerciseDetailSerializer` declares neither (absence > conditional strip).

## API endpoints

All endpoints require `IsAuthenticated`, `IsEmailVerified`, `IsInstructorUser` (identity verification not required to author — see [08-core-infrastructure.md](08-core-infrastructure.md)).
Ownership is enforced via `section__course__instructors=request.user` in every queryset filter.

### Exercise detail

```
GET    /coding-exercises/{exercise_id}/       → retrieve exercise (incl. solution + evaluation script; instructor-only)
PATCH  /coding-exercises/{exercise_id}/       → update exercise (partial)
DELETE /coding-exercises/{exercise_id}/       → delete exercise (cascades SectionContent)
```

Exercise creation is handled through the unified curriculum endpoint — one request carries the whole exercise:
```
POST /sections/{section_id}/contents/  { "item_type": "coding", ... }
```

## Authoring process (step-by-step)

1. **Create the exercise** via the unified contents endpoint — everything in one payload:
   ```
   POST /sections/{section_id}/contents/
   {
     "item_type": "coding",
     "title": "Two Sum",
     "description": "Implement two_sum(nums, target) returning the indices...",
     "language": "python",
     "starter_code": "def two_sum(nums, target):\n    pass\n",
     "solution_code": "def two_sum(nums, target): ...",
     "evaluation_script": "import unittest\nfrom exercise import two_sum\nclass T(unittest.TestCase):\n    def test_basic(self):\n        self.assertEqual(two_sum([2,7,11,15], 9), [0,1])\n",
     "time_limit_ms": 2000
   }
   ```
   Returns a `SectionContent` object; the `object_id` field is the `exercise_id`.

2. **Update details** as needed:
   ```
   PATCH /coding-exercises/{exercise_id}/
   { "evaluation_script": "..." }
   ```

3. **Reorder** the exercise in the section curriculum the same way as any other item:
   ```
   PATCH /contents/{content_id}/reorder/
   { "position": 3 }
   ```

Course submission (`_validate_course_completeness`) blocks leaving `draft` while any coding exercise has a blank `evaluation_script`.

## Evaluation-script contract (per language)

The learner's code and the instructor's evaluation script are written into the container as two files; an injected zero-dependency micro-harness runs the script's tests and reports one result per test. Assertion failure → `failed`; any other exception → `error`.

| Language | Learner file | Evaluation script contract |
|---|---|---|
| python | `/tmp/work/exercise.py` — importable as module `exercise` | Stdlib `unittest` module: `from exercise import ...`, standard `TestCase` classes. Test id (`evaluate.ClassName.test_name`) becomes the result's `test_name`. |
| javascript | `/tmp/work/exercise.js` — CommonJS, learner exports via `module.exports` | `const ex = require('./exercise')` + `node:assert`; register tests with the injected global `test(name, fn)` (async `fn` supported). |
| java | `/tmp/work/Exercise.java` — `public class Exercise` with static methods | `public class Evaluate`: every **public no-arg method named `test*`** is one test; fail by throwing `AssertionError`. Tests run in **name order** (reflection doesn't preserve declaration order). |
| cpp | learner code written verbatim to `/tmp/work/exercise.h` | `evaluate.cpp`: `#include "exercise.h"` + `#include "testkit.h"`; declare tests with `TEST(name) { ... }` using `ASSERT_TRUE / ASSERT_FALSE / ASSERT_EQ / ASSERT_NE / TESTKIT_FAIL(msg)` from the injected `testkit.h`. |

A script that registers zero tests, or a learner file that crashes at import/compile time, yields a single `evaluate (load)` error result carrying the traceback/compiler output — the learner always gets a diagnosable message, never silent emptiness.

---

# Part 2 — Learner Execution (Run / Submit)

## Two execution modes

| Mode | Endpoint | Persisted? | Returns | Poll via |
|---|---|---|---|---|
| **Run** | `POST /learn/coding-exercises/{id}/run/` | No — Celery result only (TTL = `CELERY_RESULT_EXPIRES`) | `{task_id}` (HTTP 202) | `GET /learn/coding-exercises/tasks/{task_id}/` |
| **Submit** | `POST /learn/coding-exercises/{id}/submit/` | Yes — `CodingSubmission` row | Queued submission (HTTP 202) | `GET /learn/coding-exercises/submissions/{id}/` |

Both modes run the full evaluation suite (there is no hidden/visible test split). Run is the IDE "try it" button — cheap and ephemeral. Submit is the graded attempt; the row is the permanent record used for `is_solved` and `progress_percent` calculations.

## Models

### `CodingSubmission`

| Field | Type | Notes |
|---|---|---|
| `user` | FK User | Learner who submitted |
| `exercise` | FK CodingExercise | |
| `language` | CharField(20) | One of `python` / `javascript` / `cpp` / `java` |
| `code` | TextField | Verbatim learner code |
| `status` | CharField | `queued` / `grading` / `passed` / `failed` / `error` |
| `total_tests` | PositiveInt | **0 while queued/grading** — the evaluation script decides the count, so the grading task back-fills it after the run |
| `passed_tests` | PositiveInt | Computed by the task |
| `score` | Decimal(5,2) | `round(passed_tests/total_tests*100, 2)` |
| `runtime_ms` | PositiveInt | Sum of per-test runtimes |
| `error_message`, `stdout`, `stderr` | TextField | Capped at 5000 chars each |
| `submitted_at`, `completed_at` | DateTime | `completed_at` is null until terminal |

`TERMINAL_STATUSES = (PASSED, FAILED, ERROR)`, `IN_FLIGHT_STATUSES = (QUEUED, GRADING)` — used by the idempotency check inside the task and the in-flight guard in `submit_coding_exercise`.

### `CodingSubmissionTestResult`

One row per test the evaluation script ran, in emission order:

- `test_name` — the name reported by the harness (e.g. `evaluate.AddTests.test_small`)
- `status` — `passed` / `failed` / `error`
- `stdout` — print output captured while the test ran (per-test capture, 4000-char cap)
- `stderr` — the assertion-failure message / traceback (this is the learner's feedback)
- `runtime_ms`, `exit_code`, `position`

## Endpoints

| Method | Endpoint | Permission | Purpose |
|---|---|---|---|
| GET | `/learn/coding-exercises/{exercise_id}/` | `IsLearnerUser \| IsInstructorUser` | Detail: problem + language + starter code + latest submission summary. `solution_code` / `evaluation_script` are **not declared** on the serializer (absence > conditional strip). |
| POST | `/learn/coding-exercises/{exercise_id}/run/` | `IsLearnerUser` | Dispatch transient evaluation. Returns `202 + {task_id}`. No DB row. `422` if the exercise has no evaluation script. |
| GET | `/learn/coding-exercises/tasks/{task_id}/` | `IsEmailVerified` | Poll Celery `AsyncResult`. States: `PENDING` / `STARTED` / `SUCCESS` (with `result` dict) / `FAILURE`. |
| POST | `/learn/coding-exercises/{exercise_id}/submit/` | `IsLearnerUser` | Create `CodingSubmission(status='queued', total_tests=0)` and dispatch the task on commit. Returns `202` with the queued row. `422` if the exercise has no evaluation script. |
| GET | `/learn/coding-exercises/submissions/{submission_id}/` | `IsLearnerUser` (owner only) | Poll target. Every result row is returned with its `test_name`, status, output, and failure message. |
| POST | `/learn/coding-exercises/submissions/{submission_id}/retry/` | `IsLearnerUser` (owner only) | Re-enqueue evaluation for a submission stuck in `error`. Only `error` is retryable (use `/submit/` for fresh attempts after `failed`/`passed`). |

403-vs-404 policy: all endpoints take numeric IDs → 404 for unauthorised callers (don't leak existence). Slug-based routes don't exist on this surface.

Frontend note: the immediate 202 Submit body carries `total_tests: 0` — treat 0-while-in-flight as "unknown", not "zero tests".

## Sandbox & runner contract

The Docker sandbox + per-language micro-harnesses live in `courses/services/code_runner.py`.

- **One container per submission.** Env vars: `CODE` (learner code, base64), `EVAL` (evaluation script, base64), `RUNNER` (+ `TESTKIT` for C++) carrying the injected harness. The container command decodes them to files under `/tmp/work`, compiles if needed (C++/Java inside the 32 MB tmpfs), and runs the harness.
- **Zero-dependency harnesses**: Python `unittest` (stdlib), Node `assert` + a tiny `test()` registry, Java reflection over `test*` methods, a ~100-line C++ `TEST()` macro header. The runner images need nothing beyond the base language toolchain — no jest/JUnit/gtest baked in.
- **Sentinel protocol**: the harness emits one block per test on stdout —
  `<<<SCRIPT_RESULT idx=N status=S runtime_ms=R name_len=A stdout_len=B stderr_len=C>>>` followed by the length-prefixed name / stdout / stderr bytes and `<<<SCRIPT_END idx=N>>>`. Length-prefixing means dots/spaces in test names, multi-line tracebacks, and even sentinel-lookalike learner output survive intact. `_parse_script_output` walks the stream and appends — the total count is discovered from the stream (the script decides how many tests exist).
- **Zero sentinels → synthetic error**: compile error, OOM, wall-clock timeout, or a crash before the first test produces a single `evaluation` error result carrying the container's stderr tail (e.g. the compiler output).
- **Sandbox constants**: `runtime=RUNNER_RUNTIME` (gVisor `runsc` in prod, `runc` for local dev), `network_disabled=True`, `mem_limit=128m`, `memswap_limit=128m`, `nano_cpus=500_000_000` (0.5 cores), `pids_limit=64`, `ulimits=[fsize=10MB, nproc=64, nofile=128, cpu=10s]`, `read_only=True`, `tmpfs={'/tmp': 'size=32m,exec'}`, `cap_drop=['ALL']`, `security_opt=['no-new-privileges:true']`. Wall-clock budget enforced via `container.wait(timeout=...)` + `container.kill()` on overshoot; timeout = `max(10, time_limit_ms // 1000 + 10)` (whole-suite budget + startup/compile headroom).
- **C++ harness explicitly avoids `<bits/stdc++.h>`** — pulling it under `-O2` exceeds the 128 MB memory cap and OOM-kills `cc1plus`. `testkit.h` includes only what it needs; evaluation scripts are free to add more.
- **Image overrides**: `RUNNER_IMAGE_PYTHON` / `RUNNER_IMAGE_JAVASCRIPT` / `RUNNER_IMAGE_CPP` / `RUNNER_IMAGE_JAVA`.
- **Known limitation (accepted)**: the learner's code and the evaluation script execute in the same sandboxed process, so a determined learner can read `/tmp/work/evaluate.*` at runtime and extract the assertions. This is the same trade-off Udemy makes — the sandbox contains real damage, and gaming the tests only cheats the learner. Do not attempt in-container obfuscation.
- **WARNING (echoed from CLAUDE.md)**: Docker-out-of-Docker — the daemon socket is shared with the host; a sufficiently advanced attacker can escape. Demo / single-tenant use only.

## Tasks

| Task | Decoration | Purpose |
|---|---|---|
| `evaluate_coding_run_task` | `@shared_task` (no retries) | Loads exercise + the language's evaluation script, calls `CodeRunner.run_submission`, returns a plain dict (no DB write). Infrastructure failures are reported inside the result dict — they do NOT raise into Celery `FAILURE`. |
| `evaluate_coding_submission_task` | `bind=True, acks_late=True, autoretry_for=(DockerTransientError,), max_retries=3` | Mirrors `grade_assignment_submission_task`. Early-returns on terminal status (idempotent under `acks_late` redelivery). Atomic block: bulk-insert `CodingSubmissionTestResult` rows + update aggregates (back-filling `total_tests`). On `status=passed` schedules `recalculate_progress` via `transaction.on_commit`. On final retry exhaustion: `_finalize_with_error` flips status to ERROR. |
| `reap_stuck_coding_submissions_task` | `@shared_task` (Celery beat, 60 s) | Flips `CodingSubmission` rows whose status is `queued`/`grading` for more than 5 min to `error`. Prevents polling UIs from hanging on dead workers. |

Status precedence on a verdict is **`error > failed > passed`**: if any test errors, the submission is `error`; else if any test fails, `failed`; else `passed`.

## Guard layers

1. **Service layer**: `_validate_language` rejects a submitted language that differs from `exercise.language` (400); `_validate_evaluation_script` 422s before dispatch when `evaluation_script` is blank.
2. **Task layer** (`_get_evaluation_script`): belt-and-braces re-check inside both tasks (covers an instructor blanking the script between dispatch and execution).
3. **Course lifecycle** (`_validate_course_completeness`): a course cannot leave `draft` while any coding exercise misses a script for any supported language.

`solution_code` / `evaluation_script` are never declared on any learner serializer — structurally absent, not conditionally stripped.

## Progress integration

`recalculate_progress` in `courses/services/enrollment_service.py` counts distinct PASSED coding exercises:

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

CLAUDE.md is explicit: tests must **never** hit real Docker. Patch `courses.services.code_runner.CodeRunner.run_submission` to return deterministic `ScriptTestResult` lists; Celery is in eager mode so `.delay()` runs synchronously. Coverage lives in `courses/all_tests/test_learner_coding_consumption.py`:

- Sentinel parser round-trips (dotted names, multi-line tracebacks, embedded sentinel-lookalikes, truncated tails, empty stream)
- Detail visibility (no `solution_code` / `evaluation_script` anywhere in the payload, 404 for unenrolled, instructor preview)
- Run dispatch passes the evaluation script, 422 when the language has no script, 403 for instructor, 400 for unsupported language
- Submit persistence: named result rows, `total_tests` back-filled from 0, status precedence (`error > failed > passed`), load-crash single error row, in-flight 422, owner-only 404
- Retry: only `error` is retryable, 404 for other learners
- Reaper, progress integration, `acks_late` idempotency
- Course completeness: submission blocked while a supported language misses its script

The Python and JavaScript micro-harnesses are additionally smoke-testable **without Docker** by running the harness constants in a local subprocess with `CODE`/`EVAL` env vars set. The Java and C++ harnesses require the runner containers (javac/g++) to verify end-to-end.

## System Explanation (Why This Design)

**Why script evaluation instead of I/O pairs?** Manually typing input/expected-output pairs was error-prone (nothing validated the instructor's expected output against their own solution), couldn't express multi-function or structural checks, and limited feedback to string diffs. A test script gives the instructor a full assertion vocabulary, produces named per-test feedback with real failure messages, and is the model Udemy validated for instructor-authored coding exercises.

**Why zero-dependency micro-harnesses instead of jest/JUnit/gtest?** Standard frameworks would require rebuilding all four runner images and pinning framework versions. Reflection (Java), a `test()` registry (JS), and a macro header (C++) provide the 10% of framework behaviour grading needs — named tests, assertion-vs-error distinction, per-test isolation — with zero image changes. Python gets real `unittest` because it's stdlib.

**Why is `time_limit_ms` a whole-suite budget?** The evaluation script decides how many tests exist, and that's only discoverable by running it. A per-test budget therefore can't be computed upfront; a single suite budget (+ fixed headroom for startup/compile) is honest and simple.

**Why is `total_tests` back-filled instead of snapshotted at submit time?** Same reason — the count is a property of the evaluation script's run, not of any DB row. `0` while in flight is documented as "unknown".

**Why retry only `error`, not `failed`?** `failed` is a correct verdict — the learner's code didn't pass the tests. Retrying it without new code would just produce the same `failed` again. `error` is the only status where the verdict might not reflect the code (transient Docker hiccup, load crash, etc.), so retry is meaningful.

**Why isn't Run persisted?** Run is the IDE "try it" button — it'd produce one row per keystroke-cycle learner saves. The Celery result backend with a 1-hour TTL is perfect for this: lives long enough to render in the UI, expires before it pollutes anything. The Submit path exists specifically to provide the persistent record.

**Why one container per submission?** Container startup is ~300 ms and C++/Java compiles are expensive; the harness runs every test inside one container, so a suite of N tests costs one startup + one compile. Sandbox isolation is unchanged.

## Deferred (not built)

- Hidden tests within an evaluation script (all script results are learner-visible today)
- A validate endpoint that runs the evaluation script against `solution_code` at authoring time
- LLM-assisted evaluation-script generation from the problem statement + solution
- Per-test time limits
