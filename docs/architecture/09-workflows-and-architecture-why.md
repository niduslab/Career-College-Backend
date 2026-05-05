# 09) Workflows And Architecture Why

This document explains major backend workflows in two parts:

- How it works (technical sequence)
- Why it is designed this way (architecture reason)

## 1) Authentication workflow

### How it works

1. User registers (`/api/v1/auth/register/`).
2. OTP is generated and sent to the user's email.
3. User verifies OTP (`/api/v1/auth/otp/verify/`) → `is_email_verified = True`.
4. User logs in (`/api/v1/auth/login/`) and receives `access` and `refresh` JWT tokens.
5. Protected endpoints require `IsAuthenticated` and often `IsEmailVerified`.
6. Access token (12 h) is refreshed via `POST /api/v1/auth/token/refresh/`; refresh token rotates on each use.

### Why it is used

- OTP verification reduces fake account abuse.
- Email-verified gating improves trust for marketplace actions.
- Separation of register/verify/login keeps auth flow auditable and flexible.
- Rotating refresh tokens limit exposure window of compromised tokens.

---

## 2) Profile workflow

### How it works

1. `User` is created with a `user_type` (`learner`, `instructor`, `partner_institution`, `admin`).
2. A signal auto-creates the matching profile model row (`LearnerProfile`, `InstructorProfile`, or `PartnerInstitutionProfile`).
3. Profile fields are managed through private endpoints (`/auth/profile/me/`).
4. Education/work history rows are attached separately.
5. Public listing/detail endpoints expose only intended public data.

### Why it is used

- Keeps auth identity (`User`) clean and stable.
- Allows profile fields to evolve per user type without bloating one table.
- Supports role-specific UI and filtering (learners vs instructors vs institutions).

---

## 3) Course creation workflow

### How it works

1. Verified instructor (`IsVerifiedInstructor`) creates `NidusCourse`.
2. Adds learning objectives, prerequisites, and audience entries through separate metadata endpoints.
3. Adds sections with `position` (unique per course).
4. Adds section contents (lecture / quiz / coding exercise) via `POST /sections/{id}/contents/`.

### Why it is used

- Mirrors real course-authoring UX in steps.
- Metadata tables are independent, so frontend can autosave parts safely without locking the whole course record.
- Instructor ownership checks (`section__course__instructors=request.user`) are enforced uniformly at the queryset level.

---

## 4) Curriculum ordering workflow (`SectionContent`)

### How it works

1. Every curriculum item (Lecture, Quiz, CodingExercise) gets a `SectionContent` row when created.
2. `SectionContent.position` defines order within the section; the content object itself has no position field.
3. Reorder API (`PATCH /contents/{content_id}/reorder/`) calls `reorder_section_content()`:
   - Locks all rows in the section with `SELECT FOR UPDATE`.
   - Moves the item to a temp position to avoid unique constraint collision.
   - Shifts neighbors by 1 in the correct direction.
   - Sets the item's final position.
4. `GenericRelation` on each content model cascades `SectionContent` deletion when the content object is deleted.
5. Reindex management command repairs gaps if historical data drift exists.

### Why it is used

- Single ordering system for all mixed content types — no duplication of position logic in Lecture, Quiz, or CodingExercise models.
- `SELECT FOR UPDATE` locking prevents race conditions when two instructors edit the same section concurrently.
- Two-phase position shift (via temp offset) avoids hitting the unique `(section, position)` constraint mid-transaction.
- Makes drag-and-drop reorder logic consistent and predictable regardless of content type.

---

## 5) Lecture + video processing workflow

### How it works

1. Lecture is created via `POST /sections/{id}/contents/` with `item_type="lecture"` (or directly).
2. For video uploads, `PATCH /lectures/{id}/` with `video_file` calls `replace_lecture_video_and_enqueue_transcoding()`:
   - Marks the previous `VideoAsset` inactive.
   - Creates a new active `VideoAsset`.
   - Creates a `VideoProcessingJob`.
   - Enqueues `transcode_video_asset_task` on Celery.
3. Celery worker runs FFmpeg to produce 5 HLS renditions (240p, 360p, 480p, 720p, 1080p).
4. On success: `VideoAsset.status → ready`, `Lecture.stream_master_playlist` and `stream_renditions` are populated.
5. On failure: `VideoAsset.status → failed`; task retries up to 3 times with exponential backoff.

### Why it is used

- Upload API stays fast; heavy transcoding is async.
- Historical `VideoAsset` rows are retained while only one is active — supports rollback without data loss.
- `VideoProcessingJob` statuses enable monitoring, retry logic, and support debugging.

---

## 6) Quiz workflow

### How it works

1. Quiz is created either:
   - via `POST /sections/{id}/contents/` with `item_type="quiz"` (curriculum-first), or
   - via `POST /quizzes/` directly (then placed via `SectionContent`).
2. Section placement is represented by a `SectionContent` row.
3. Instructor adds questions to `/quizzes/{quiz_id}/questions/` in ordered sequence.
4. Instructor adds answer options to `/quiz-questions/{question_id}/answers/`.
5. Serializer and DB constraint enforce: at most one `is_correct=True` answer per question.

### Why it is used

- Supports both curriculum-first UI (drag-and-drop builder) and direct resource APIs.
- Question/answer split keeps data normalized and extensible.
- DB + serializer constraints protect quiz content integrity at two levels.

---

## 7) Identity verification workflow

### How it works

1. Instructor creates a draft verification request (`POST /verification/create/`).
2. Uploads identity documents and submits (`POST /verification/{id}/submit/`).
3. Admin reviews via `/verification/admin/{id}/review/` and transitions the status.
4. Allowed transitions (enforced in model `transition_to()`):
   - `draft → submitted`
   - `submitted → under_review | expired`
   - `under_review → approved | rejected | action_required | expired`
   - `action_required → submitted | expired`
5. On `approved`: `InstructorProfile.is_verified = True` is set automatically.

### Why it is used

- State-machine transitions prevent invalid or skipped review steps.
- Required-field checks at submission ensure reviewable quality.
- Coupling approval to `InstructorProfile.is_verified` keeps `IsVerifiedInstructor` permission consistent.

---

## 8) Coding exercise authoring workflow

### How it works

1. Instructor adds exercise to a section via `POST /sections/{section_id}/contents/` with `item_type="coding"`.
   - Backend creates a `CodingExercise` row and a `SectionContent` row in one transaction.
   - Response includes the `exercise_id`.
2. Instructor adds per-language configurations via `POST /coding-exercises/{exercise_id}/language-configs/`:
   - Each config has `language`, `starter_code`, and `solution_code`.
   - Unique constraint `(exercise, language)` prevents duplicate language configs; violation returns 400.
3. Instructor adds test cases via `POST /coding-exercises/{exercise_id}/testcases/`:
   - Each test case has `input_data`, `expected_output`, `position`, `is_hidden`, and optional `explanation`.
   - `is_hidden=True` marks grading-only cases never returned to learners.
   - Unique constraint `(exercise, position)` enforces ordered positions.
4. When a test case is deleted:
   - The delete and the subsequent position re-gap are wrapped in `transaction.atomic()`.
   - All test cases with `position > deleted_position` are decremented by 1 in a single `UPDATE` using `F('position') - 1`.
5. Exercise metadata (title, description, difficulty, time limit) is updated via `PATCH /coding-exercises/{exercise_id}/`.

### Why it is used

- Multi-step authoring (exercise → configs → test cases) mirrors the real content-authoring workflow without a complex nested serializer.
- Keeping `solution_code` in a separate model (`CodingExerciseLanguageConfig`) that is only served through instructor-gated endpoints prevents accidental leakage in learner APIs.
- Atomic delete-and-reposition keeps test case positions contiguous, which simplifies client-side rendering without requiring a gap-tolerant UI.
- Hidden test cases decouple the grading set from the learning set without a separate model.

---

## 9) Why service/selector layering exists

### How it works

- Views handle HTTP parsing and permissions.
- Serializers handle validation and response shaping only — no DB writes or business logic.
- Services handle domain operations (curriculum ordering, video pipeline setup).
- Selectors centralize reusable query logic (base querysets, instructor-scoped fetches).

### Why it is used

- Keeps views small and focused on HTTP concerns.
- Improves testability: service and selector logic can be tested without HTTP context.
- Reduces duplicate ordering and query code across endpoints.
- Serializers stay predictable — they never trigger side effects or external calls.
