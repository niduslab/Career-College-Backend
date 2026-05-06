# 04) Courses And Curriculum

## Key files

- `courses/models.py`: course domain and curriculum models
- `courses/urls.py`: endpoint map
- `courses/views.py`: export layer
- `courses/all_views/course_views.py`: core course endpoints
- `courses/all_views/content_views.py`: section, content, lecture, quiz, question, answer APIs
- `courses/all_views/coding_views.py`: coding exercise, language config, and test case APIs
- `courses/serializers.py`: validation and response serialization
- `courses/selectors.py`: query helpers
- `courses/services.py`: business logic helpers (ordering, create helpers)
- `core/permissions.py`: auth and instructor permission classes

## Core models

### `CourseCategory`

- `name`, `slug`, `description`, `parent` (self-referential FK for subcategories), `is_active`, `display_order`

### `NidusCourse`

- Relations:
  - `created_by` (FK -> `User`)
  - `instructors` (M2M -> `User`)
  - `partner_institutions` (M2M -> `PartnerInstitutionProfile`)
  - `category` (FK -> `CourseCategory`)
- Metadata:
  - `title`, `slug`, `description`, `thumbnail`
  - `price`, `language`, `level` (`beginner|intermediate|advanced`)
  - `duration_minutes`
  - `status` (`draft|under_review|published|rejected|archived`)
  - `is_published` (denormalized flag), `rejection_reason`, `published_at`

### Supporting text tables

All three are normalized 1-to-many off `NidusCourse` and support independent autosave from a course builder UI:

- `CourseLearningObjective` (`course`, `text`, `display_order`)
- `CoursePreRequisite` (`course`, `text`, `display_order`)
- `CourseAudience` (`course`, `text`, `display_order`)

## Section and curriculum models

### `CourseSection`

- `course`, `title`, `description`, `position`
- Unique ordering per course: `(course, position)`

### `SectionContent` (curriculum backbone)

- `section` (FK -> `CourseSection`)
- `item_type` discriminator: `lecture|quiz|assignment|coding`
- Generic relation fields:
  - `content_type` (FK -> Django `ContentType`)
  - `object_id` (PK of the target object)
  - `content_object` (GenericForeignKey — resolves to `Lecture`, `Quiz`, or `CodingExercise`)
- `position` (order inside section)
- Unique constraint: `(section, position)`

`SectionContent` is the **single ordering layer** for all mixed content in a section. Content objects (`Lecture`, `Quiz`, `CodingExercise`) do not carry position fields themselves.

### `Lecture`

- `section` (FK)
- `title`, `content_type` (`video|article`), `article_content`
- Streaming fields: `stream_master_playlist`, `stream_renditions`, `transcoding_error`
- Has `GenericRelation` to `SectionContent` — ensures cascade delete removes the `SectionContent` slot automatically.
- No `position` field; order is owned by `SectionContent`.

### `Quiz`

- `section` (FK), `title`, `description`
- `related_lectures` (M2M -> `Lecture`)
- Has `GenericRelation` to `SectionContent` for cascade delete.

### `CodingExercise`

- `section` (FK)
- `title`, `description`, `problem_statement`
- `difficulty` (`easy|medium|hard`)
- `default_language`, `supported_languages` (JSON list)
- `time_limit_ms`
- Has `GenericRelation` to `SectionContent` for cascade delete.
- Related: `language_configs` (reverse FK to `CodingExerciseLanguageConfig`), `test_cases` (reverse FK to `CodingTestCase`)

For full detail on the coding exercise sub-models (`CodingExerciseLanguageConfig`, `CodingTestCase`) and their endpoints, see `10-coding-exercises.md`.

## Main API surface

From `courses/urls.py`:

- Courses:
  - `GET /courses/`
  - `POST /courses/create/`
  - `GET/PATCH/DELETE /courses/{id}/`
- Course metadata:
  - objectives, prerequisites, audiences (list/create/detail per type)
- Sections:
  - `GET/POST /courses/{course_id}/sections/`
  - `GET/PATCH/PUT/DELETE /sections/{section_id}/`
- Section curriculum:
  - `GET/POST /sections/{section_id}/contents/`
  - `PATCH /contents/{content_id}/reorder/`
- Lectures:
  - `GET /sections/{section_id}/lectures/`
  - `GET/PATCH/PUT/DELETE /lectures/{lecture_id}/`
- Quizzes:
  - `POST /quizzes/`
  - `GET/PATCH/DELETE /quizzes/{quiz_id}/`
  - `GET/POST /quizzes/{quiz_id}/questions/`
  - `GET/PATCH/DELETE /quiz-questions/{question_id}/`
  - `GET/POST /quiz-questions/{question_id}/answers/`
  - `GET/PATCH/DELETE /quiz-answers/{answer_id}/`
- Coding exercises:
  - `GET/PATCH/DELETE /coding-exercises/{exercise_id}/`
  - `GET/POST /coding-exercises/{exercise_id}/language-configs/`
  - `GET/PATCH/DELETE /coding-exercises/{exercise_id}/language-configs/{config_id}/`
  - `GET/POST /coding-exercises/{exercise_id}/testcases/`
  - `GET/PATCH/DELETE /coding-exercises/{exercise_id}/testcases/{tc_id}/`

## Curriculum creation process

1. Instructor creates a section.
2. Instructor adds any curriculum item through `POST /sections/{section_id}/contents/` with `item_type`:
   - `"lecture"` — creates a `Lecture` and a `SectionContent` row in one atomic transaction.
   - `"quiz"` — creates a `Quiz` and a `SectionContent` row.
   - `"coding"` — creates a `CodingExercise` and a `SectionContent` row.
3. The `content_id` in the response is the `SectionContent.id`, used for reordering.
4. Reorder endpoint updates `SectionContent.position` and shifts affected items atomically.
5. After a coding exercise is created, the instructor adds language configs and test cases via the coding exercise sub-endpoints.

## Ordering utilities

- `courses/services.py`:
  - `create_section_content_for_object(section, obj, item_type)` — creates the `SectionContent` slot for any domain object.
  - `reorder_section_content(section, content_id, new_position)` — atomic reorder with `SELECT FOR UPDATE` locking and two-phase position shifting.
  - `get_next_section_content_position(section)` — returns the next available position in a section.
- `courses/management/commands/reindex_section_content_positions.py`:
  - Repairs a section (or all sections) to contiguous positions `1..n`. Use after manual DB edits or data migrations.

## Workflow

1. Instructor creates course and baseline metadata.
2. Instructor creates ordered sections for the course.
3. Instructor adds mixed content items (lecture / quiz / coding exercise) into sections via the unified contents endpoint.
4. `SectionContent` tracks position for every curriculum item.
5. Reorder endpoint shifts neighboring items atomically.
6. For coding exercises, language configs and test cases are added through sub-endpoints after exercise creation.

## System Explanation (Why This Design)

- Normalized metadata tables make course authoring modular and allow frontend to autosave individual fields independently.
- `SectionContent` decouples placement/order from content object details — adding a new content type (e.g., `assignment`) requires no change to the ordering system.
- `GenericRelation` on each content model ensures `SectionContent` rows are deleted automatically when the content object is deleted, without custom delete signals.
- Service-layer reorder logic prevents fragile manual position handling in views.
