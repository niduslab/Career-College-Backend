# 11) Assignments CRUD (Instructor Side)

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
- Fields: `question_text`, `model_answer`, `points`, `hint`, `position`
- Constraint: `(assignment, position)` must be unique
- Meaning: one assignment cannot have two questions in the same position

## Security and visibility rules

1. Authentication and instructor checks happen in views (`IsAuthenticated`, `IsEmailVerified`, and write operations require `IsVerifiedInstructor`).
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
