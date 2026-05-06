# 10) Coding Exercises

Coding exercises are interactive programming problems that instructors author as curriculum items. This document covers the data model, authoring API, and design decisions for Part 1 (CRUD only). Learner-facing execution and evaluation are not yet implemented.

## Key files

- `courses/models.py`: `CodingExercise`, `CodingExerciseLanguageConfig`, `CodingTestCase`
- `courses/all_views/coding_views.py`: all coding exercise endpoints
- `courses/serializers.py`: `CodingExerciseSerializer`, `CodingExerciseCreateUpdateSerializer`, `CodingExerciseLanguageConfigSerializer`, `CodingTestCaseSerializer`
- `courses/urls.py`: route definitions
- `core/permissions.py`: `IsVerifiedInstructor` (gates all coding endpoints)

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

**Security rule**: `solution_code` must never appear in any learner-facing serializer or response. It is included in `CodingExerciseLanguageConfigSerializer` only because all coding endpoints require `IsVerifiedInstructor`.

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

All endpoints require `IsAuthenticated`, `IsEmailVerified`, `IsVerifiedInstructor`.
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
