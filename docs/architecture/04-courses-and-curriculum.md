# 04) Courses And Curriculum

## Key files

- `courses/models.py`: course domain and curriculum models
- `courses/urls.py`: endpoint map
- `courses/views.py`: export layer
- `courses/all_views/course_views.py`: core course endpoints
- `courses/all_views/content_views.py`: section, content, lecture, quiz, question, answer APIs
- `courses/serializers.py`: validation and response serialization
- `courses/selectors.py`: query helpers
- `courses/services.py`: business logic helpers (ordering, create helpers)
- `core/permissions.py`: auth and instructor permission classes

## Core models

## `CourseCategory`

- `name`, `slug`, `description`, `parent`, `is_active`, `display_order`

## `NidusCourse`

- Relations:
  - `created_by` (FK -> `User`)
  - `instructors` (M2M -> `User`)
  - `partner_institutions` (M2M)
  - `category` (FK -> `CourseCategory`)
- Metadata:
  - `title`, `slug`, `description`, `thumbnail`
  - `price`, `language`, `level`, `duration_minutes`
  - `status` (`draft|under_review|published|rejected|archived`)
  - `is_published`, `rejection_reason`, `published_at`

## Supporting text tables

- `CourseLearningObjective` (`course`, `text`, `display_order`)
- `CoursePreRequisite` (`course`, `text`, `display_order`)
- `CourseAudience` (`course`, `text`, `display_order`)

## Section and curriculum models

## `CourseSection`

- `course`, `title`, `description`, `position`
- Unique ordering per course: `(course, position)`

## `SectionContent` (curriculum backbone)

- `section`
- `item_type`: `lecture|quiz|assignment|coding`
- Generic relation:
  - `content_type`
  - `object_id`
  - `content_object`
- `position` (order inside section)

This is the single ordering layer for mixed content in sections.

## Main API surface

From `courses/urls.py`:

- Courses:
  - `GET /courses/`
  - `POST /courses/create/`
  - `GET/PATCH/DELETE /courses/{id}/`
- Course metadata:
  - objectives, prerequisites, audiences
- Sections:
  - list/create/detail
- Section curriculum:
  - `GET/POST /courses/sections/{section_id}/contents/`
  - `PATCH /courses/contents/{content_id}/reorder/`

## Curriculum creation process

1. Instructor creates section.
2. Instructor adds curriculum item through `sections/{id}/contents/`.
3. Backend creates domain object (`Lecture` or `Quiz`) plus `SectionContent` slot.
4. Reorder endpoint updates `SectionContent.position` and shifts affected items.

## Ordering utilities

- `courses/services.py`:
  - `create_section_content_for_object(...)`
  - `reorder_section_content(...)`
  - `get_next_section_content_position(...)`
- `courses/management/commands/reindex_section_content_positions.py`:
  - Repairs a section (or all sections) to contiguous positions `1..n`.

## Workflow

1. Instructor creates course and baseline metadata.
2. Instructor creates ordered sections for the course.
3. Instructor adds mixed content items (lecture/quiz) into sections.
4. `SectionContent` tracks position for every curriculum item.
5. Reorder endpoint shifts neighboring items atomically.

## System Explanation (Why This Design)

- Normalized metadata tables make course authoring modular and easier to autosave.
- `SectionContent` decouples placement/order from content object details.
- Service-layer reorder logic prevents fragile manual position handling in views.
