# 04) Courses And Curriculum

## Key files

| File | Purpose |
|------|---------|
| `courses/all_models/course_models.py` | `NidusCourse`, `CourseSection`, `SectionContent`, `CourseCategory`, metadata text tables |
| `courses/all_models/content_models.py` | `Lecture`, `VideoAsset`, `VideoProcessingJob`, `WatchProgress` |
| `courses/all_views/course_views.py` | Course list/create/detail (instructor authoring surface) |
| `courses/all_views/content_views.py` | Sections, SectionContent, lectures, quizzes |
| `courses/all_views/coding_views.py` | Coding exercises, language configs, test cases |
| `courses/all_views/assignment_views.py` | Assignment CRUD |
| `courses/services/section_service.py` | Reorder logic, video pipeline entry point |
| `courses/urls.py` | All course URL patterns |
| `core/permissions.py` | `IsVerifiedInstructor`, `IsCourseInstructor` |

---

## Core models

### `CourseCategory`

- `name`, `slug` (unique)
- `description`
- `parent` (self-referential FK → `CourseCategory`, nullable) — supports subcategories
- `is_active`, `display_order`

### `NidusCourse`

The top-level course entity.

**Relations:**
- `created_by` (FK → `User`)
- `instructors` (M2M → `User`) — all instructors who own/edit this course
- `partner_institution` (FK → `PartnerInstitutionProfile`, nullable, `SET_NULL`) — set automatically at creation when the creator is a partner institution; never writable via the API
- `category` (FK → `CourseCategory`)

**Metadata:**
- `title`, `slug` (auto-generated from title, unique)
- `description`, `thumbnail`
- `price` (Decimal), `language`, `level` (`beginner | intermediate | advanced`)
- `duration_minutes`

**Status:**
- `status` — `draft | under_review | published | rejected | archived`
- `is_published` — denormalized boolean flag for fast filtering
- `rejection_reason` — set by admin when rejecting
- `published_at` — timestamp when status first becomes `published`

**IMPORTANT:** Never set `status` directly. Always call `NidusCourse.transition_to(new_status)`.
See `11-course-lifecycle.md` for the full state machine and completeness checks.

### Multi-instructor support

A course has two instructor-related fields with different semantics:

- `created_by` — the **owner**. Set once at creation, immutable via the API. Only the owner can modify the instructor list.
- `instructors` — **all co-authors** (M2M). Any instructor in this set can read and edit course content (sections, lectures, quizzes, assignments, videos). The owner is always a member of this set.
- `partner_institution` — the owning institution (FK), set automatically at creation and never writable via the API. Partner-institution roster changes flow through the dedicated `institution-instructors` endpoints, not the course PATCH.

Roster changes (adding/removing co-instructors) are restricted to the owner inside `NidusCourseCreateUpdateSerializer.update()`. A co-instructor PATCH that includes the `instructors` field has it silently ignored — the other fields in the payload still apply.

See `13-multi-instructor-collaboration.md` for the full role table, enforcement details, and future extensions.

### Supporting text tables

Normalized 1-to-many off `NidusCourse`. Support independent autosave from a course builder UI:

| Model | Fields |
|-------|--------|
| `CourseLearningObjective` | `course`, `text`, `display_order` |
| `CoursePreRequisite` | `course`, `text`, `display_order` |
| `CourseAudience` | `course`, `text`, `display_order` |

---

## Section and curriculum models

### `CourseSection`

- `course` (FK → `NidusCourse`)
- `title`, `description`
- `position` — integer ordering within the course
- **Unique constraint:** `(course, position)`

### `SectionContent` — the curriculum backbone

`SectionContent` is the **single source of truth for ordering** within a section. Every curriculum
item — regardless of type — gets one `SectionContent` row. The content object itself has no
`position` field.

```
CourseSection
    │
    ├── SectionContent (position=1) → GenericFK → Lecture (id=5)
    ├── SectionContent (position=2) → GenericFK → Quiz (id=2)
    ├── SectionContent (position=3) → GenericFK → CodingExercise (id=8)
    └── SectionContent (position=4) → GenericFK → Assignment (id=3)
```

**Fields:**
- `section` (FK → `CourseSection`)
- `item_type` — discriminator: `lecture | quiz | assignment | coding`
- `content_type` (FK → Django `ContentType`)
- `object_id` (PositiveIntegerField — PK of the target object)
- `content_object` (GenericForeignKey — resolves to the actual object)
- `position` (ordering within section, 1-based)
- **Unique constraint:** `(section, position)`

**Cascade delete:** Each content model (`Lecture`, `Quiz`, `CodingExercise`, `Assignment`) has a
`GenericRelation` to `SectionContent`. When the content object is deleted, its `SectionContent`
row is automatically deleted via Django's `GenericRelation` cascade — no custom delete signal needed.

### `Lecture`

- `section` (FK → `CourseSection`)
- `title`
- `lecture_type` — `video | article`
- `article_content` (TextField, blank for video lectures)
- `stream_master_playlist` — denormalized HLS master playlist path (set after transcoding)
- `stream_renditions` (JSONField) — denormalized rendition list
- `transcoding_error` (TextField) — stores error if transcoding fails
- `is_preview` (BooleanField) — if `True`, unenrolled catalog visitors can stream this lecture
- `GenericRelation` to `SectionContent` — ensures cascade delete
- No `position` field — order is owned by `SectionContent`

### `Quiz`

- `section` (FK → `CourseSection`)
- `title`, `description`
- `related_lectures` (M2M → `Lecture`) — optional contextual link
- `GenericRelation` to `SectionContent`

### `CodingExercise`

- `section` (FK → `CourseSection`)
- `title`, `description`, `problem_statement`
- `difficulty` (`easy | medium | hard`)
- `default_language`, `supported_languages` (JSONField list)
- `time_limit_ms`
- `GenericRelation` to `SectionContent`
- Related: `language_configs` (reverse FK → `CodingExerciseLanguageConfig`), `test_cases` (reverse FK → `CodingTestCase`)

See `09-coding-exercises.md` for full coding exercise details.

### `Assignment`

- `section` (FK → `CourseSection`)
- `title`, `description`, `instructions`
- `passing_score` (PositiveIntegerField)
- `GenericRelation` to `SectionContent`
- Related: `questions` (reverse FK → `AssignmentQuestion`)

---

## Curriculum creation process

All curriculum items are created through the unified content endpoint:

```
POST /api/v1/courses/sections/{section_id}/contents/
  body: { "item_type": "lecture"|"quiz"|"coding"|"assignment", ...fields }
         │
         ▼
View identifies item_type
         │
         ▼
In a single atomic transaction:
  1. Create domain object (Lecture / Quiz / CodingExercise / Assignment)
  2. Get next position: max(SectionContent.position for section) + 1
  3. Create SectionContent row linking the object + assigning position
         │
         ▼
Response includes SectionContent.id (used for reordering)
and the created object's id
```

After creation, additional sub-resources are added via their own endpoints:
- Quiz: add questions → answers
- Coding exercise: add language configs → test cases
- Assignment: add questions

---

## Reorder algorithm (two-phase shift)

`reorder_section_content()` in `courses/services/section_service.py` handles atomic reordering.
A naive single-pass update would hit the `(section, position)` unique constraint mid-update,
so a two-phase approach is used.

**Algorithm:**

```
PATCH /api/v1/courses/contents/{content_id}/reorder/
  body: { "position": <new_position> }
         │
         ▼
1. SELECT FOR UPDATE — lock all SectionContent rows in section
   (prevents concurrent reorder conflicts)
         │
         ▼
2. Clamp target: new_position = min(new_position, max_current_position)
         │
         ▼
3. Move item to temp position = max_position + 1
   (safely out of range — avoids collision)
         │
         ▼
4a. Moving item UP (target < current position):
    Rows in [target_position .. current_position - 1] shift DOWN by 1
    Phase A: position += large_offset (e.g. +1000) — avoid constraint
    Phase B: position -= (large_offset - 1) — land at +1

4b. Moving item DOWN (target > current position):
    Rows in [current_position + 1 .. target_position] shift UP by 1
    Phase A: position += large_offset
    Phase B: position -= (large_offset + 1) — land at -1
         │
         ▼
5. Move item from temp to target_position
         │
         ▼
6. Refresh content object from DB
```

**Example — move C from position 3 to position 2:**

```
Initial:   A(1)  B(2)  C(3)  D(4)
Step 3:    A(1)  B(2)  D(4)  C(5)   ← C moved to temp (5)
Step 4a:   A(1)  B(3)  D(4)  C(5)   ← B shifted down to 3
Step 5:    A(1)  C(2)  B(3)  D(4)   ← C placed at target (2)
```

---

## Main API surface

```
# Course CRUD
GET    /api/v1/courses/                           → paginated list (instructor's own)
POST   /api/v1/courses/create/                    → create course
GET    /api/v1/courses/{id}/                      → detail (instructor authoring surface)
PATCH  /api/v1/courses/{id}/                      → partial update metadata
DELETE /api/v1/courses/{id}/                      → delete course

# Course metadata (independent autosave)
GET/POST   /api/v1/courses/{id}/objectives/
GET/POST   /api/v1/courses/{id}/prerequisites/
GET/POST   /api/v1/courses/{id}/audiences/
GET/PATCH/DELETE  /api/v1/courses/{id}/objectives/{obj_id}/
# ... (same pattern for prerequisites, audiences)

# Sections
GET/POST   /api/v1/courses/{course_id}/sections/
GET/PATCH/DELETE  /api/v1/courses/sections/{section_id}/

# Unified curriculum (create any content type)
GET/POST   /api/v1/courses/sections/{section_id}/contents/
PATCH      /api/v1/courses/contents/{content_id}/reorder/

# Lectures
GET        /api/v1/courses/sections/{section_id}/lectures/
GET/PATCH/DELETE  /api/v1/courses/lectures/{lecture_id}/

# Quizzes
POST       /api/v1/courses/quizzes/
GET/PATCH/DELETE  /api/v1/courses/quizzes/{quiz_id}/
GET/POST   /api/v1/courses/quizzes/{quiz_id}/questions/
GET/PATCH/DELETE  /api/v1/courses/quiz-questions/{question_id}/
GET/POST   /api/v1/courses/quiz-questions/{question_id}/answers/
GET/PATCH/DELETE  /api/v1/courses/quiz-answers/{answer_id}/

# Coding exercises
GET/PATCH/DELETE  /api/v1/courses/coding-exercises/{exercise_id}/
GET/POST          /api/v1/courses/coding-exercises/{exercise_id}/language-configs/
GET/PATCH/DELETE  /api/v1/courses/coding-exercises/{exercise_id}/language-configs/{config_id}/
GET/POST          /api/v1/courses/coding-exercises/{exercise_id}/testcases/
GET/PATCH/DELETE  /api/v1/courses/coding-exercises/{exercise_id}/testcases/{tc_id}/

# Course status transitions
POST  /api/v1/courses/{id}/submit/   → draft → under_review
POST  /api/v1/courses/{id}/review/   → under_review → published | rejected  (admin only)
POST  /api/v1/courses/{id}/rework/   → rejected → draft
POST  /api/v1/courses/{id}/archive/  → published → archived
```

---

## Ordering utilities

**`courses/services/section_service.py`:**
- `create_section_content_for_object(section, obj, item_type)` — creates the `SectionContent`
  slot for any domain object
- `reorder_section_content(section_content, new_position)` — atomic reorder with `SELECT FOR UPDATE`
  and two-phase position shifting
- `get_next_section_content_position(section)` — returns `max(position) + 1` for a section

**Management command:**
- `python manage.py reindex_section_content_positions [--dry-run]` — repairs gaps in positions
  after manual DB edits or data migrations. Resets positions to contiguous `1..n`.

---

## Why this design

- **Normalized metadata tables** (objectives, prerequisites, audiences) allow frontend to autosave
  individual fields independently without locking the entire course record.
- **`SectionContent` as the single ordering layer** decouples placement/order from content object
  details — adding a new content type requires no change to the ordering system. The new model
  just needs a `GenericRelation` to `SectionContent`.
- **`GenericRelation` on each content model** ensures `SectionContent` rows are deleted
  automatically when the content object is deleted, without custom delete signals or view-level
  cleanup code.
- **Two-phase reorder** (temp position → shift neighbors → place final) avoids hitting the unique
  `(section, position)` constraint mid-transaction, which would happen with a naive single-pass UPDATE.
- **`SELECT FOR UPDATE`** in the reorder prevents race conditions when two instructors edit the
  same section curriculum concurrently.
- **Unified `POST /contents/` endpoint** creates both the domain object and its `SectionContent`
  slot in one atomic transaction — prevents orphaned content with no curriculum placement.
 