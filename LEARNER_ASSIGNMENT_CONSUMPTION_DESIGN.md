# Learner Assignment Consumption — Design

**Status:** Planned. No code yet.
**Scope:** Phase-2 remainder for the learner-facing surface — attend an assignment, submit it, have it **auto-graded against an instructor-authored rubric**, see results. Companion to the already-shipped quiz consumption flow.
**Prereqs in place:** Assignment authoring (instructor CRUD for `Assignment` + `AssignmentQuestion`), section-content ordering, `recalculate_progress` already reserves the `completed_assignments` slot, Celery worker already running for the video pipeline.

## What changed from the previous draft

The previous draft assumed **instructor manual grading**. This revision swaps it for **deterministic rubric-based auto-grading** executed asynchronously by a Celery task. Consequences:

- `AssignmentQuestion` gains a `rubric` field that stores structured criteria.
- Instructor grading endpoints are removed. There are no instructor-facing endpoints for submissions in v1 — the data sits in the DB and is queryable by admins. Manual override is a Phase-3 addition.
- A new submission state `grading` (and a failure state `grading_failed`) is introduced — the submit request returns `202` and the learner polls or relies on the frontend's existing query-refresh pattern.
- A new Celery task `grade_assignment_submission_task` and a `RubricGrader` service implement the grading logic.
- A `POST /retry/` endpoint re-enqueues the same submission when it's stuck in `grading_failed`.
- `model_answer` reveal rule stays the same (post-grading only).

## Goal

Let an enrolled learner:

1. Open an assignment from the curriculum sidebar and read the questions.
2. Submit free-text answers to all questions in one POST.
3. Have the submission auto-graded against the rubric, with per-question score + per-criterion feedback.
4. See per-question feedback and the instructor's `model_answer` **only after grading completes** with a terminal verdict (`passed` or `failed`).
5. Retry the grader if a submission ends in `grading_failed` (re-enqueues the same submission row).
6. Resubmit after a terminal verdict — `passed`, `failed`, or `grading_failed` (resubmit creates a new submission row; retry reuses the existing one).

## Architectural decisions

Each decision below was weighed against alternatives during planning and locked in. Reopen explicitly before changing.

| # | Decision | Rationale | Cost to revisit |
|---|---|---|---|
| 1 | **Deterministic rubric-based auto-grading.** The instructor authors a structured rubric per question; a `RubricGrader` evaluates each criterion against the learner's `answer_text` and sums points. No LLM, no manual grading. | Rule-based grading is reproducible, free, and testable. LLM grading is a future additive layer (new criterion `type`), not a v1 dependency. | Medium — additive criterion types are easy; ripping out deterministic grading entirely would require a different storage shape. |
| 2 | **Rubric lives on `AssignmentQuestion` as one `JSONField`.** Holds a list of criteria objects — `[{type, value, points, feedback_on_match, feedback_on_miss}, ...]`. | Single field on the existing model, no new table, but expressive enough for keyword / regex / length checks. JSONField gives DB-level shape validation for free on Postgres (the production DB). | Low — migrating a JSONField to a normalized criterion table later is straightforward (one data-migration script). |
| 3 | **Async grading via Celery.** `POST /submit/` creates the submission with `status='grading'` and enqueues `grade_assignment_submission_task`. The task evaluates the rubric, writes per-answer scores, recomputes `total_score`, transitions status to `passed` / `failed` / `grading_failed`, and schedules `recalculate_progress` via `transaction.on_commit`. | Mirrors the video transcoding pattern (`tasks.py`) — same broker, same worker, same retry shape. Keeps `/submit/` latency tight even if rubric size grows. Future LLM grading drops in with no surgery. | Low. |
| 4 | **Pass logic.** `total_score >= assignment.passing_score` → `passed`; otherwise `failed`. Status set by the Celery task; never set inline by a view. | Deterministic + identical to quiz pass logic. | Low. |
| 5 | **Multi-attempt with explicit retry.** Resubmission allowed only after a prior submission reached a terminal state (`passed`, `failed`, or `grading_failed`). One in-flight submission per `(user, assignment)` at any time — in-flight means `status in ('submitted', 'grading')`. A separate `POST /retry/` endpoint re-enqueues a `grading_failed` submission without creating a new row. | Prevents Celery queue flooding from a single learner. Retry reuses the same row so `submitted_at` and historical correlation stay correct; resubmission creates a fresh row when the learner wants to take another shot. | Low — relaxing in-flight is dropping the partial unique constraint; removing retry collapses to "always resubmit." |
| 6 | **`model_answer` visibility to learner.** Never auto-exposed. Included in the learner-side submission-detail payload **only when** `status in ('passed', 'failed')`. Hidden during `submitted` / `grading` / `grading_failed`. | Prevents pre-grading reveal; a `grading_failed` submission shouldn't leak the model answer either since the learner can retry or resubmit. | Trivial (one branch in the builder function). |
| 7 | **Snapshotted `max_score` and rubric.** `AssignmentSubmission.max_score` snapshots `assignment.total_score` at submit time (the instructor-declared total — see decision 9). `AssignmentSubmissionAnswer.max_score` is set from `question.points` at submit time. The rubric **snapshot** is stored alongside the answer (see data model) so that an instructor editing the rubric after submission doesn't retroactively rewrite past grades. | Same invariant the quiz `QuizAttemptAnswer.is_correct` denormalization preserves: historical submissions stay frozen. | High once shipped. |
| 9 | **Assignment-level `total_score` is instructor-declared, not derived.** `Assignment.total_score` is a stored field the instructor sets directly; it's the learner-facing denominator and the value `passing_score` is validated against (`passing_score <= total_score`). The sum of `question.points` is **not** required to equal `total_score` — questions are a sub-allocation guide, the declared total is authoritative. The serializer also exposes a computed `max_score` (sum of `question.points`) so the authoring UI can show "you've allocated X of Y points." | Decouples the learner-facing total from the question budget so the instructor can declare a worth before writing rubric criteria, and so historical submissions snapshot a meaningful denominator even if the question set changes. Adds one field with a clear validator. | Low — additive field with a backfill migration. |
| 8 | **No instructor read endpoints in v1.** Auto-grading runs without instructor action, so listing/inspecting submissions adds no learner-facing value in v1. Admin / analytics dashboards can hit the DB directly. Manual override is a Phase-3 addition (new endpoint, audit fields, override-aware status). | Cuts ~2 views, ~2 serializers, and a service module from v1 with zero impact on the learner flow. | Low — additive later, no schema change required. |

## Rubric format

`AssignmentQuestion.rubric` is a `JSONField` containing a **list of criterion objects**. Empty list = no auto-grading possible (the authoring serializer should reject this when a course transitions out of `draft`, but allow it during draft authoring).

```json
[
  {
    "type": "keyword",
    "value": "gradient descent",
    "case_sensitive": false,
    "points": 3,
    "feedback_on_match": "Correctly identifies the core algorithm.",
    "feedback_on_miss": "Missing reference to the gradient descent algorithm."
  },
  {
    "type": "regex",
    "value": "\\blearning[_ ]rate\\b",
    "points": 2,
    "feedback_on_match": "Mentions the learning rate hyperparameter.",
    "feedback_on_miss": ""
  },
  {
    "type": "min_length",
    "value": 100,
    "points": 5,
    "feedback_on_match": "Answer is sufficiently detailed.",
    "feedback_on_miss": "Answer is too short — aim for at least 100 characters."
  }
]
```

**Criterion types (v1):**

| `type` | `value` shape | Match rule |
|---|---|---|
| `keyword` | string | `value.lower() in answer.lower()` (or case-sensitive if `case_sensitive: true`) |
| `regex` | string (valid Python regex) | `re.search(value, answer, flags)` returns a match |
| `min_length` | int (chars) | `len(answer.strip()) >= value` |
| `max_length` | int (chars) | `len(answer.strip()) <= value` |
| `any_of` | list of strings | At least one keyword present (case-insensitive) |
| `all_of` | list of strings | All keywords present (case-insensitive) |

**Invariants enforced by the authoring serializer:**

- `sum(criterion.points) == question.points`. The criterion-points sum must equal the question's stated max — no orphan points, no over-budget rubrics.
- Each criterion has `type`, `value`, `points`; `feedback_on_match` / `feedback_on_miss` are optional.
- `regex` criteria are compiled at save time to catch syntax errors early.
- Unknown `type` rejected at save time.

## Data model

Two new tables + one new field on the existing question table, all in [courses/all_models/assessment_models.py](courses/all_models/assessment_models.py).

### Modify `AssignmentQuestion`

```python
class AssignmentQuestion(models.Model):
    # ... existing fields ...
    rubric = models.JSONField(
        default=list,
        blank=True,
        help_text='List of grading criteria; sum of criterion.points must equal points.',
    )
```

Migration: default empty-list so existing rows backfill cleanly.

### New: `AssignmentSubmission`

```python
class AssignmentSubmission(models.Model):
    class Status(models.TextChoices):
        SUBMITTED      = 'submitted'        # row created, task not yet picked up
        GRADING        = 'grading'          # Celery task is running
        PASSED         = 'passed'           # graded; total_score >= passing_score
        FAILED         = 'failed'           # graded; below passing_score
        GRADING_FAILED = 'grading_failed'   # task exhausted retries

    user             = FK(User, on_delete=CASCADE, related_name='assignment_submissions')
    assignment       = FK(Assignment, on_delete=CASCADE, related_name='submissions')
    submitted_at     = DateTimeField(auto_now_add=True)
    graded_at        = DateTimeField(null=True, blank=True)
    status           = CharField(choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    total_score      = PositiveIntegerField(default=0)
    max_score        = PositiveIntegerField()                 # snapshot of assignment.total_score at submit time
    grading_error    = TextField(blank=True, default='')       # populated on grading_failed

    class Meta:
        ordering = ['-submitted_at']
        indexes = [models.Index(fields=['user', 'assignment'])]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'assignment'],
                condition=Q(status__in=['submitted', 'grading']),
                name='uniq_inflight_assignment_submission',
            ),
        ]
```

### New: `AssignmentSubmissionAnswer`

```python
class AssignmentSubmissionAnswer(models.Model):
    submission        = FK(AssignmentSubmission, on_delete=CASCADE, related_name='answers')
    question          = FK(AssignmentQuestion, on_delete=CASCADE, related_name='+')
    answer_text       = TextField(blank=True)
    score             = PositiveIntegerField(default=0)
    max_score         = PositiveIntegerField()                  # snapshot of question.points
    rubric_snapshot   = JSONField(default=list)                 # frozen copy of question.rubric at submit time
    criterion_results = JSONField(default=list)                 # per-criterion verdict from the grader
    feedback          = TextField(blank=True, default='')        # composed summary the learner sees

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['submission', 'question'], name='uniq_submission_question'),
        ]
```

`criterion_results` shape (written by the grader):

```json
[
  {"index": 0, "type": "keyword", "matched": true, "points_awarded": 3, "feedback": "..."},
  {"index": 1, "type": "regex",   "matched": false, "points_awarded": 0, "feedback": "..."}
]
```

### Why snapshot the rubric per answer and not just per submission

Per-answer snapshot keeps the join shape flat for both the grader and the read API: a single `submission.answers.all()` returns everything needed to render the result UI, no fan-out back into `AssignmentQuestion`. Submission-level snapshot would force a second query keyed by question to render per-criterion verdicts. The duplication cost is small (rubrics are tiny JSON).

### Why a partial unique constraint and not a service-level check

Postgres-level enforcement is concurrency-safe with zero application code. A `.exists()` check in `submit_assignment` would race against a concurrent POST from the same learner.

**Caveat:** SQLite doesn't support partial unique constraints with `Q()` conditions. Local test runners that use SQLite will skip the constraint. CLAUDE.md mandates Postgres for development, so this should be a non-issue, but we keep a belt-and-braces `.exists()` guard inside the atomic block anyway.

## Endpoint surface

All paths under `/api/v1/courses/`. No instructor-facing submission endpoints in v1.

| Method | URL | View | Purpose |
|---|---|---|---|
| GET  | `/learn/assignments/<int:assignment_id>/` | `LearnerAssignmentDetailView` | Assignment metadata + questions (no `model_answer`, no `rubric`) + caller's `latest_submission` summary. |
| POST | `/learn/assignments/<int:assignment_id>/submit/` | `LearnerAssignmentSubmitView` | Body: `{answers: [{question_id, answer_text}, ...]}`. Creates submission + N answer rows + enqueues Celery task in one transaction. Returns **`202 Accepted`** with the new submission's id and `status='grading'`. `422` if an in-flight submission already exists. |
| GET  | `/learn/assignments/submissions/<int:submission_id>/` | `LearnerAssignmentSubmissionDetailView` | Learner sees own only. Per-question score + `criterion_results` + `feedback`; `model_answer` iff `status in (passed, failed)`. Returns `status` so the client can poll. |
| POST | `/learn/assignments/submissions/<int:submission_id>/retry/` | `LearnerAssignmentSubmissionRetryView` | Re-enqueue grading for a `grading_failed` submission. Resets `status` to `grading` and dispatches the Celery task again. `422` if status is not `grading_failed`. |

### Status-code policy (per [CLAUDE.md](CLAUDE.md))

All assignment endpoints use numeric IDs in the URL → **404 with generic message** ("Assignment not found." / "Submission not found.") when the caller lacks access. Never 403 on these surfaces; never leak existence via the message body.

## Service layer split

```
courses/services/
├── learner_service.py             # ADD: get_assignment_for_consumption,
│                                  #      submit_assignment,
│                                  #      get_learner_assignment_submission,
│                                  #      retry_assignment_grading
├── assignment_grading.py          # NEW: RubricGrader (criterion type → match function map),
│                                  #      grade_submission(submission) — called from Celery task
└── enrollment_service.py          # MODIFY: implement completed_assignments in recalculate_progress
```

`RubricGrader` is a class (not free functions) only because the criterion-type → match-function dispatch table benefits from being attribute-accessible — easy to extend, easy to mock in tests. The public surface is one method: `RubricGrader().grade(answer_text, rubric_snapshot, max_score) → (score, criterion_results, feedback)`.

`submit_assignment` is responsible for: (a) validating the in-flight constraint, (b) creating the parent + bulk-creating answer rows with `rubric_snapshot` populated, (c) `transaction.on_commit(lambda: grade_assignment_submission_task.delay(submission.id))`. Never enqueue before commit — a rolled-back transaction must not leak a task into the queue.

`retry_assignment_grading` is responsible for: (a) verifying the submission belongs to the caller and is in `grading_failed`, (b) flipping `status` back to `grading` + clearing `grading_error`, (c) `transaction.on_commit(...)` re-dispatch.

## Celery task

Location: `courses/tasks.py` (alongside `transcode_video_asset_task`).

```python
@shared_task(
    bind=True,
    acks_late=True,                            # ack only after the task body completes
    autoretry_for=(Exception,),
    retry_backoff=True, retry_jitter=True, max_retries=3,
)
def grade_assignment_submission_task(self, submission_id: int):
    submission = AssignmentSubmission.objects.select_for_update().get(pk=submission_id)
    if submission.status not in (Status.SUBMITTED, Status.GRADING):
        return  # idempotent — already terminal

    submission.status = Status.GRADING
    submission.save(update_fields=['status'])

    try:
        with transaction.atomic():
            answers = list(submission.answers.all())
            grader = RubricGrader()
            for answer in answers:
                score, results, feedback = grader.grade(
                    answer.answer_text, answer.rubric_snapshot, answer.max_score,
                )
                answer.score = score
                answer.criterion_results = results
                answer.feedback = feedback
            AssignmentSubmissionAnswer.objects.bulk_update(
                answers, ['score', 'criterion_results', 'feedback'],
            )

            submission.total_score = sum(a.score for a in answers)
            submission.graded_at = timezone.now()
            submission.status = (
                Status.PASSED
                if submission.total_score >= submission.assignment.passing_score
                else Status.FAILED
            )
            submission.save(update_fields=['total_score', 'graded_at', 'status'])

            if submission.status == Status.PASSED:
                enrollment = Enrollment.objects.filter(
                    user=submission.user, course=submission.assignment.section.course,
                ).first()
                if enrollment:
                    transaction.on_commit(lambda: recalculate_progress(enrollment))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            submission.status = Status.GRADING_FAILED
            submission.grading_error = str(exc)[:1000]
            submission.save(update_fields=['status', 'grading_error'])
            return
        raise
```

`acks_late=True` + `select_for_update` + early-return makes the task **idempotent** under retries and double-dispatch. With `acks_late=True`, Celery only acks the message after the task body finishes — if the worker dies mid-task, the broker redelivers and the next invocation picks up where it left off (or short-circuits if status is already terminal).

## Serializer split

```
courses/all_serializers/
└── learner_serializers.py              # ADD: LearnerAssignmentDetailSerializer (no model_answer, no rubric),
                                        #      LearnerAssignmentQuestionSerializer,
                                        #      LearnerSubmissionDetailSerializer,
                                        #      AssignmentSubmissionInputSerializer,
                                        #      build_assignment_submission_result(submission)
```

**Authoring side:** the existing `AssignmentQuestionSerializer` (used by instructor CRUD) gains a `rubric` field with validator `validate_rubric` that:

- ensures it's a list,
- compiles each `regex` value,
- rejects unknown `type`s,
- enforces `sum(criterion.points) == validated_data['points']` when both are present.

It also strips `rubric` from the response when the caller is not an instructor (same pattern already in use for `model_answer`).

**Critical:** do **not** reuse `AssignmentQuestionSerializer` for the learner-facing payload. Per CLAUDE.md, *absence is a stronger guarantee than conditional removal*. Define `LearnerAssignmentQuestionSerializer` that simply doesn't declare `model_answer` or `rubric` — same pattern as `_LearnerQuizAnswerOptionSerializer`.

`build_assignment_submission_result` is a function, not a class — same as `build_quiz_attempt_result`. The "reveal model answer only when status in (passed, failed)" rule lives there.

## N+1 audit

| Operation | Risk | Mitigation |
|---|---|---|
| `GET /learn/assignments/<id>/` | Per-question loop hitting DB; latest-submission lookup | `select_related('section__course')` + `prefetch_related('section__course__instructors', Prefetch('questions', queryset=AssignmentQuestion.objects.order_by('position').only('id', 'question_text', 'points', 'hint', 'position')))` + a single `.filter(...).order_by('-submitted_at').first()` for the summary. **Do not** `.only(...)` `rubric` here — learner endpoint never reads it. |
| `POST /learn/assignments/<id>/submit/` | One INSERT per answer + rubric fetch per question | Single `.in_bulk()` for the assignment's questions to grab `points` + `rubric` once; build `AssignmentSubmissionAnswer` objects in memory; one `bulk_create`. Wrap in `@transaction.atomic`. |
| Celery task | Per-answer UPDATE | Already addressed: one round-trip to fetch answers, one `bulk_update`, one save on the submission. |
| `recalculate_progress` | Already does one grouped query per item type | Add `completed_assignment_ids = set(AssignmentSubmission.objects.filter(user=..., assignment_id__in=assignment_ids, status='passed').values_list('assignment_id', flat=True))`. Same shape as the quiz path. |

## Progress integration

[courses/services/enrollment_service.py:170](courses/services/enrollment_service.py#L170-L173) currently has the reserved hook:

```python
completed_assignments = 0  # reserved
```

Replace with the set-intersection pattern used for quizzes (counting `status='passed'` submissions). The trigger path is inside `grade_assignment_submission_task` — see the snippet above. `on_commit` deferral is identical to the quiz pattern: a recalc failure can't roll back a valid grade.

No-op on `failed`, `grading_failed`, or re-grading retries within the same passing state — avoids unnecessary recomputation.

## Build order

Each step is independently deployable and reviewable. Tests land alongside the code in each step.

1. **Rubric field + authoring validation.** Add `AssignmentQuestion.rubric` (JSONField). Update `AssignmentQuestionSerializer` with `validate_rubric` and strip the field for non-instructor responses. Migration.
   - **Tests:** valid rubric saves; sum-of-points mismatch rejected with 400; bad regex rejected; unknown criterion `type` rejected; non-instructor caller never sees `rubric` in the response.

2. **Submission models & migration.** Add `AssignmentSubmission`, `AssignmentSubmissionAnswer`. `makemigrations` + `migrate`.
   - **Checkpoint:** save one of each in the shell; verify the partial unique constraint blocks a second in-flight submission under Postgres.

3. **`RubricGrader` service.** Pure-Python class, no view layer yet.
   - **Tests:** each criterion type matches as specified; case sensitivity respected; clamps `score` to `max_score` even if rubric sums incorrectly (defense in depth); empty rubric returns `(0, [], '')` without crashing.

4. **Learner read.** `get_assignment_for_consumption` service + `LearnerAssignmentDetailSerializer` + `LearnerAssignmentDetailView`.
   - **Tests:** enrolled learner sees payload without `model_answer` and without `rubric`; instructor preview sees same shape; unenrolled → 404; `latest_submission` appears once one exists.

5. **Learner submit + Celery task.** `submit_assignment` service + `LearnerAssignmentSubmitView` + `grade_assignment_submission_task`.
   - **Tests:** happy path returns 202 with `status='grading'`; `CELERY_TASK_ALWAYS_EAGER=True` in tests so we can assert the final terminal state inside the test; rejects duplicate `question_id`; rejects `question_id` not in this assignment; 422 if in-flight submission exists; instructor caller → 403; unenrolled → 404; `rubric_snapshot` populated correctly per answer; task is idempotent under double-dispatch.

6. **Learner own-submission detail.** `GET /learn/assignments/submissions/<id>/`.
   - **Tests:** own submission visible; other learner's submission → 404; `model_answer` hidden while `submitted` / `grading` / `grading_failed`, exposed once `passed` or `failed`; `criterion_results` returned.

7. **Retry endpoint.** `POST /learn/assignments/submissions/<id>/retry/`.
   - **Tests:** retry from `grading_failed` re-enqueues and reaches terminal state; retry from `passed` / `failed` / `grading` / `submitted` → 422; retry on someone else's submission → 404; `grading_error` cleared on retry.

8. **Progress integration.** Wire `recalculate_progress`. Mirror the quiz on-commit test pattern.
   - **Tests:** `captureOnCommitCallbacks(execute=True)`; a passing grade triggers exactly one callback; failing or grading_failed triggers zero; progress percent updates as expected.

9. **Docs sweep.** Update [CLAUDE.md](CLAUDE.md) "Learner Consumption Endpoints" table, [COURSES_API_TESTING_GUIDE.md](COURSES_API_TESTING_GUIDE.md) (new section 12B.10+ with rubric payload examples up front since the frontend form is not yet built), [LEARNER_COURSE_CONSUMPTION_DESIGN.md](LEARNER_COURSE_CONSUMPTION_DESIGN.md) (mark assignments as built), and the [13-enrollment.md](docs/architecture/13-enrollment.md) endpoint table. Add `AssignmentQuestion.rubric` to the **learner-safe serialization** table in CLAUDE.md (instructor-only field).

## Risk flags

- **Rubric authoring UX.** Free-text JSON in the admin/instructor UI is unfriendly. v1 ships the API; the structured frontend form is a hard prereq before non-technical instructors get the feature (gate via feature flag / nav link absence until form lands). Strict serializer validation is the safety net during the transitional period — see decision #2 and the authoring serializer's `validate_rubric`.
- **Empty rubric on a published assignment.** Authoring serializer should reject `rubric=[]` when a question is part of a course transitioning out of `draft`. Until that hook is in place, an empty rubric will produce `score=0` and a likely `failed` verdict for everyone — flag this in the course-publish validation pass.
- **Stuck `grading` submissions.** A normal Celery worker outage is *not* a risk here: tasks queue in the broker and run when the worker comes back. The real risks are narrower:
  1. **Broker data loss.** Redis is in-memory by default. Configure persistence (`appendonly yes` for Redis, or use a durable broker) so queued tasks survive a Redis restart.
  2. **Enqueue failure on `on_commit`.** DB commits, then the broker is unreachable at that exact instant. Submission row exists, task was never queued. Mitigation: a periodic sweeper that finds `submitted` / `grading` submissions older than N minutes and either re-enqueues them or marks them `grading_failed`. Out of scope for v1; document the operational risk.
  3. The `acks_late=True` decorator handles worker-died-mid-task — the message redelivers and the next invocation either resumes or short-circuits because status is already terminal.
- **Resubmission UX.** After `failed`, the learner can submit again. After `grading_failed`, they can either retry (re-enqueue the same row via `POST /retry/`) or submit again (new row). The frontend must surface the prior submission's `criterion_results` next to the new submission form. Out of scope for the backend, but flag in the API testing guide.
- **Snapshot drift.** Once a submission is graded, the instructor can edit the live `rubric` freely; past submissions stay frozen because `rubric_snapshot` was copied at submit time. This is intentional — same invariant as `QuizAttemptAnswer.is_correct`.
- **Partial unique constraint portability.** Postgres-only. CLAUDE.md mandates Postgres so this is documented but not mitigated further.

## Resolved decisions (previously open)

1. **Rubric authoring scope for v1:** Backend API ships now; structured frontend form is required before non-technical instructors get the feature. API testing guide carries example payloads for internal users / frontend devs.
2. **`grading_failed` recovery:** Re-enqueue the same submission row via the dedicated `POST /retry/` endpoint. Keeps `submitted_at` and historical correlation correct.
3. **Notifications when a submission is graded:** Out of scope for v1. The grading task does not emit a signal.
4. **Criterion type lock-in:** v1 ships `keyword | regex | min_length | max_length | any_of | all_of`. Adding more types later is additive (new entry in the dispatch table); renaming/removing is a data migration on `rubric_snapshot`.
