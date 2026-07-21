# 10) Assignments CRUD (Instructor Side)

This document explains how assignment operations work from an instructor's perspective, then maps each action to what happens internally in models, views, serializers, and services.

## Key files

- `courses/models.py`: `Assignment`, `AssignmentQuestion`
- `courses/serializers.py`: `AssignmentSerializer`, `AssignmentCreateUpdateSerializer`, `AssignmentQuestionSerializer`
- `courses/all_views/assignment_views.py`: instructor-facing assignment endpoints
- `courses/services/assignment_service.py`: business logic for create/update/delete/reorder
- `courses/urls.py`: route definitions

## Instructor-facing endpoints

All routes below are under `/api/v1/courses/`.

### Assignments

```text
GET    sections/{section_id}/assignments/
POST   sections/{section_id}/assignments/
GET    assignments/{assignment_id}/
PATCH  assignments/{assignment_id}/
DELETE assignments/{assignment_id}/
```

### Questions inside an assignment

```text
GET    assignments/{assignment_id}/questions/
POST   assignments/{assignment_id}/questions/
GET    assignment-questions/{question_id}/
PATCH  assignment-questions/{question_id}/
DELETE assignment-questions/{question_id}/
PATCH  assignments/{assignment_id}/questions/reorder/
POST   assignments/rubric-preview/          (stateless rubric generator; see below)
```

## What an instructor experiences

Think of an assignment as a container (title, instructions, passing score), and questions as ordered items inside that container.

1. You create an assignment in a section.
2. You add questions one by one.
3. You edit assignment details or question details any time.
4. If you delete a question, the system automatically re-numbers later questions to keep order clean.
5. If you delete an assignment, its questions go away automatically.

## Data model (what is saved)

## `Assignment`

- Belongs to one `CourseSection`
- Fields: `title`, `description`, `instructions`, `passing_score`
- Also linked to `SectionContent` (generic relation) so it appears in curriculum ordering

## `AssignmentQuestion`

- Belongs to one `Assignment`
- Fields: `question_text`, `model_answer`, `rubric`, `points`, `hint`, `position`
- `points` defaults to **0** (a new question is worth nothing until the
  instructor sets a value — no implicit default weight).
- `rubric` (JSONField, instructor-only) is the auto-grading answer key — a list
  of criterion objects; see *Auto-grading* and *Auto-generated rubric* below.
  `model_answer` is instructor-only reference text (never used by the grader).
- Constraint: `(assignment, position)` must be unique
- Meaning: one assignment cannot have two questions in the same position

## Security and visibility rules

1. Authentication and instructor checks happen in views (`IsAuthenticated`, `IsEmailVerified`, and write operations require `IsInstructorUser` — identity verification not required to author; see [08-core-infrastructure.md](08-core-infrastructure.md)).
2. Ownership checks ensure the assignment belongs to a course taught by the current instructor.
3. `model_answer` is instructor-only in serializer output. Non-instructors do not receive it.

## How view + serializer + service + model work together

Simple flow pattern for most operations:

1. **View** receives request and checks permission/ownership.
2. **Serializer** validates input fields (shape and basic rules).
3. **Service** performs the business operation in a transaction.
4. **Model/DB** persists changes and enforces constraints.
5. **View** serializes the response and returns HTTP status/message.

## Operation-by-operation internals

## 1) Create assignment

Route:
- `POST sections/{section_id}/assignments/`

Internal sequence:
1. View validates payload with `AssignmentCreateUpdateSerializer`.
2. View calls `create_assignment(section_id, user, validated_data)`.
3. Service creates `Assignment`.
4. Service also creates a `SectionContent` row so assignment appears in section curriculum.
5. Transaction ensures both records are created together.

Why this matters:
- Instructors never end up with an assignment that exists but is missing from curriculum placement.

## 2) List assignments in a section

Route:
- `GET sections/{section_id}/assignments/`

Internal sequence:
1. View verifies section ownership.
2. Queries assignments and prefetches questions.
3. Serializes using `AssignmentSerializer`.
4. `max_score` is computed as sum of question points.

## 3) Get assignment detail

Route:
- `GET assignments/{assignment_id}/`

Internal sequence:
1. View ensures assignment belongs to instructor's course.
2. Serializer returns assignment + nested questions.
3. Question ordering is controlled by `position`.

## 4) Update assignment

Route:
- `PATCH assignments/{assignment_id}/`

Internal sequence:
1. View validates partial data via `AssignmentCreateUpdateSerializer`.
2. Service `update_assignment(...)` applies only allowed fields:
   - `title`, `description`, `instructions`, `passing_score`
3. Saves assignment and returns updated object.

## 5) Delete assignment

Route:
- `DELETE assignments/{assignment_id}/`

Internal sequence:
1. View confirms ownership.
2. Service `delete_assignment(...)` deletes the assignment.
3. Question rows are deleted automatically via foreign-key cascade.
4. Related `SectionContent` slot is also removed via generic relation cascade.

## 6) Create question

Route:
- `POST assignments/{assignment_id}/questions/`

Internal sequence:
1. View validates payload with `AssignmentQuestionSerializer`.
2. View keeps only writable fields (`question_text`, `model_answer`, `points`, `hint`).
3. Service `add_question(...)` computes next position (`max + 1`).
4. Service uses transaction and row locking to reduce concurrent position conflicts.
5. Question is created with auto-assigned `position`.

## 7) List questions

Route:
- `GET assignments/{assignment_id}/questions/`

Internal sequence:
1. View fetches assignment.
2. Questions are ordered by `position`, then `id`.
3. Serializer returns question list.
4. `model_answer` visibility depends on user role.

## 8) Update question

Route:
- `PATCH assignment-questions/{question_id}/`

Internal sequence:
1. View verifies ownership and validates payload.
2. View passes allowed fields only.
3. Service `update_question(...)` updates and saves.

## 9) Delete question (position compaction)

Route:
- `DELETE assignment-questions/{question_id}/`

Internal sequence:
1. Service stores deleted question's `position`.
2. Deletes that question row.
3. Bulk-updates all later questions in same assignment:
   - `position = position - 1` for rows with `position > deleted_position`

Result:
- Positions remain contiguous with no gaps.
- Example: `1, 2, 3, 4` delete `2` -> `1, 2, 3`.

## 10) Reorder questions manually

Route:
- `PATCH assignments/{assignment_id}/questions/reorder/`

Internal sequence:
1. Service validates submitted ID list:
   - no duplicates
   - exact same set as existing questions
2. Uses a two-phase offset update to avoid temporary unique-constraint collisions.
3. Applies final positions in requested order.

## Error behavior

1. Validation issues return `400` with field errors.
2. Missing or unauthorized resources return `404` via ownership queries.
3. Unexpected failures return generic `500` response.
4. Server logs keep full traceback using `logger.exception(...)` for debugging.

## Quick mental model for instructors

- "Assignment" is the top-level task in a section.
- "Questions" are ordered items inside that task.
- Creating/deleting assignment also manages curriculum placement automatically.
- Question positions are automatically maintained so numbering stays clean.

---

## Assignment auto-grading (learner side)

Assignment grading is asynchronous. The submission endpoint returns `202 Accepted` immediately;
a Celery task performs the actual grading and updates the row.

### Key files (learner side)

| File | Purpose |
|------|---------|
| `courses/all_models/assessment_models.py` | `AssignmentSubmission`, `AssignmentSubmissionAnswer` |
| `courses/services/learner_service.py` | `submit_assignment()`, `get_learner_assignment_submission()`, `retry_assignment_grading()` |
| `courses/services/assignment_grading.py` | `RubricGrader` — deterministic per-criterion scoring |
| `courses/tasks.py` | `grade_assignment_submission_task` |
| `courses/all_serializers/learner_serializers.py` | `build_assignment_submission_result()` |

### Submission and grading flow

```
POST /api/v1/courses/learn/assignments/{id}/submit/
  Permission: IsLearnerUser
  body: { "answers": [{ "question_id": 1, "answer_text": "..." }, ...] }
         │
         ▼
submit_assignment(user, assignment, answers_payload, enrollment)
  [courses/services/learner_service.py]
         │
         ├─ Check for in-flight submission: status in (submitted, grading) for (user, assignment)
         │   → 422 if one exists (prevents parallel double-submissions)
         │
         ├─ Atomic transaction:
         │   AssignmentSubmission(status='submitted',
         │     max_score=assignment.total_score)  ← snapshotted
         │   For each question:
         │     AssignmentSubmissionAnswer(
         │       answer_text=...,
         │       rubric_snapshot=question.rubric,  ← frozen at submit time
         │       max_score=question.points          ← frozen at submit time
         │     )
         │
         └─ transaction.on_commit:
              grade_assignment_submission_task.delay(submission.id)
         │
         ▼
202 Accepted — { submission_id, status: "submitted" }

──────────────────────────────────────────────────────────
Celery worker: grade_assignment_submission_task
  Decorator: @shared_task(bind=True, acks_late=True,
               autoretry_for=(Exception,), max_retries=3)
──────────────────────────────────────────────────────────
         │
         ├─ Early return if status already terminal (idempotent under acks_late redelivery)
         │
         ├─ status → 'grading'
         │
         ├─ For each AssignmentSubmissionAnswer:
         │   RubricGrader.grade(answer_text, rubric_snapshot, max_score)
         │   → returns (score, criterion_results, feedback)
         │
         ├─ total_score = sum of answer scores
         │
         ├─ if total_score >= assignment.passing_score:
         │     submission.status = 'passed'
         │     → transaction.on_commit: recalculate_progress(enrollment)
         │   else:
         │     submission.status = 'failed'
         │
         ▼
Learner polls: GET /learn/assignments/submissions/{id}/
  → Returns status, per-answer score/feedback
  → model_answer per question revealed ONLY when status in (passed, failed)
  → Hidden during: submitted, grading, grading_failed
```

### `RubricGrader` — deterministic criterion matchers

The grader lives in `courses/services/assignment_grading.py`. Each question's `rubric` is a JSON
object defining one or more criteria:

```json
{
  "criteria": [
    { "type": "keyword", "value": "HTTP", "points": 2, "feedback_on_match": "Good.", "feedback_on_miss": "Mention HTTP." },
    { "type": "regex", "value": "REST.*stateless", "points": 3 },
    { "type": "min_length", "value": 50, "points": 1 },
    { "type": "any_of", "value": ["REST", "RESTful"], "points": 2 }
  ]
}
```

Each criterion carries an integer **`points`** (awarded in full when the criterion matches, zero
otherwise) and optional **`feedback_on_match`** / **`feedback_on_miss`** strings surfaced to the
learner per criterion.

**Supported criterion types:**

| Type | Value | Passes when |
|------|-------|-------------|
| `keyword` | string | Answer contains the keyword (case-insensitive) |
| `regex` | pattern | Answer matches the regex pattern |
| `min_length` | int | `len(answer_text.strip()) >= value` |
| `max_length` | int | `len(answer_text.strip()) <= value` |
| `any_of` | list of strings | Answer contains at least one item from the list |
| `all_of` | list of strings | Answer contains all items from the list |

Score formula — sum of awarded points, clamped to `max_score`:
```
score = min( sum(criterion.points for each matching criterion), max_score )
```

The grader is **defensive**: an unknown criterion type or a matcher that raises an exception
is recorded as a miss, not a crash. Grading completes even if one criterion is malformed.

### Auto-generated rubric from the model answer (Option B)

An empty rubric grades every answer to **0** (the grader short-circuits on
`not rubric_snapshot`). To avoid that silent hole when an instructor writes a
Model Answer but skips the rubric, authoring **auto-generates a multi-group
`all_of` rubric from the model answer at save time**.

- Logic: `courses/services/rubric_autogen.py` → `generate_rubric_from_model_answer(model_answer, points, max_terms=5)`.
  Pure function (no Django imports). Extracts the top-N content words (length ≥ 3,
  stopwords removed) by frequency — ties broken alphabetically for determinism —
  and splits them into **several `all_of` groups**, dividing the points across
  the groups. Group count = `min(points, keyword_count)` (one point per group
  at most, one keyword per group at least); keywords and points are each spread
  as evenly as possible with the remainder on the earliest groups, so the
  points sum equals `question.points` exactly (fallback path) and passes
  `_validate_rubric_criteria`. In the manual-points path every group's points
  are 0. E.g. a 2-point question with keywords `[a,b,c,d,e]` →
  `all_of[a,b,c]=1pt` + `all_of[d,e]=1pt`.
- Wiring: `_autofill_rubric(question)` in `assignment_service.py`, called by both
  `add_question` and `update_question` **before save**. It only fires when the
  resulting question has a non-empty `model_answer` **and** an empty `rubric` — an
  instructor-authored rubric is never overwritten.
- Fallback of the fallback: if the model answer yields no usable keyword (blank,
  only stopwords, or `points == 0`), the rubric stays empty and grading keeps its
  existing score-0 behavior. A question with **no rubric and no model answer**
  still grades to 0.
- Preview: `POST /api/v1/courses/assignments/rubric-preview/` (`IsInstructorUser`,
  body `{model_answer, points, max_terms?}`) returns the generated `all_of`
  groups **without saving** and with **`points: 0`** on every group
  (`split_points=False`) — the instructor assigns each group's points in the
  UI, and the authoring UI blocks Save until the sum equals the question's
  points. `points` is still sent because it decides the group count. Stateless,
  not tied to a question id.
- `split_points` flag on `generate_rubric_from_model_answer`: `True` (silent
  fallback) divides `question.points` across the `all_of` groups so a
  fully-skipped rubric still grades; `False` (preview) sets 0 for manual point
  entry (grouping is unchanged).

### Retry for grading_failed

```
POST /api/v1/courses/learn/assignments/submissions/{id}/retry/
  Permission: IsLearnerUser (own submission only)
  → Only allowed when status == 'grading_failed'
  → Resets status to 'grading', clears grading_error
  → Re-dispatches grade_assignment_submission_task on commit
  → submitted_at preserved (historical correlation intact)
  → 422 for any non-grading_failed status
  → 404 for another learner's submission
```

### Adding a new rubric criterion type

`_MATCHERS` dict in `assignment_grading.py` maps type string → matcher function.
`_RUBRIC_CRITERION_VALUE_VALIDATORS` in the authoring serializer validates the criterion's `value`
at save time. Adding a new type is additive: register a matcher in the grader and a validator in
the serializer — no other code changes needed.
