# 12) Course Lifecycle — Creation, Submission, and Review

This document covers the full journey of a course from first creation through admin review to publication (and beyond). It describes every state, every transition, every guard, and which code owns each step.

---

## Key files

| File | Responsibility |
|------|---------------|
| `courses/models.py` | `NidusCourse` model, `VALID_TRANSITIONS`, `transition_to()`, `_validate_course_completeness()` |
| `courses/all_views/course_views.py` | `CourseCreateAPIView`, `CourseDetailView`, `CourseListAPIView` |
| `courses/all_views/status_views.py` | `CourseSubmitForReviewView`, `CourseAdminReviewView`, `CourseReworkView`, `CourseArchiveView` |
| `courses/serializers.py` | `NidusCourseCreateUpdateSerializer`, `NidusCourseSerializer` |
| `courses/urls.py` | URL wiring for all course endpoints |
| `core/permissions.py` | `IsVerifiedInstructor`, `IsAdminUser`, `IsCourseInstructor` |

---

## State machine overview

A course moves through five statuses. Every transition is guarded inside `NidusCourse.transition_to()` — no other code path changes `status`.

```
                    submit               approve
  draft  ─────────────────►  under_review  ──────────►  published  ──►  archived
    ▲                               │                                       │
    │                        reject │                                       │
    │                               ▼                                       │
    └─────────────────────  rejected                        draft  ◄────────┘
           rework
```

### `VALID_TRANSITIONS` (in `courses/models.py`)

```python
VALID_TRANSITIONS = {
    'draft':        ('under_review',),
    'under_review': ('published', 'rejected'),
    'rejected':     ('draft',),
    'published':    ('archived',),
    'archived':     ('draft',),
}
```

Any attempt to move to a status not in this map raises `ValidationError` immediately.

---

## Phase 1 — Course Creation

### Who can create

Permission chain on `CourseCreateAPIView`:

```
IsAuthenticated + IsEmailVerified + IsVerifiedInstructor
```

`IsVerifiedInstructor` checks that `InstructorProfile.is_verified == True`. An instructor must have completed identity verification before this endpoint is accessible.

### What the endpoint does

```
POST /api/v1/courses/create/
```

`NidusCourseCreateUpdateSerializer` accepts:

| Field | Required | Notes |
|-------|----------|-------|
| `title` | Yes | Min 5 characters |
| `description` | No | Can be added later |
| `thumbnail` | No | Image upload |
| `price` | No | Decimal |
| `language` | No | |
| `level` | No | `beginner / intermediate / advanced` |
| `duration_minutes` | No | |
| `category` | No | FK to `CourseCategory` |
| `instructors` | No | Additional instructor user PKs |
| `partner_institutions` | No | Partner institution PKs |
| `learning_objectives` | No | List of objective texts |
| `prerequisites` | No | List of prerequisite texts |
| `audiences` | No | List of audience texts |

`status` and `rejection_reason` are **not** in the serializer. A new course always starts as `draft`. Status can only change through the dedicated transition endpoints.

### Response

```json
{
  "success": true,
  "message": "Course created successfully.",
  "data": {
    "id": 1,
    "title": "Python Backend Bootcamp",
    "status": "draft",
    ...
  }
}
```

---

## Phase 2 — Building Course Content

Before a course can be submitted, it must pass a completeness check. The instructor builds content using these endpoints:

### 1. Add sections

```
POST /api/v1/courses/{course_id}/sections/create/
body: { "title": "Getting Started", "position": 1 }
```

At least one section is required for submission.

### 2. Add curriculum items to each section

All content goes through a single creation endpoint:

```
POST /api/v1/courses/sections/{section_id}/contents/
```

The `item_type` field routes creation:

| `item_type` | Model created | Key fields |
|-------------|--------------|------------|
| `lecture` | `Lecture` + `SectionContent` | `lecture_type` (article/video), `article_content` or `video_file` |
| `quiz` | `Quiz` + `SectionContent` | `title`, `description` |
| `coding` | `CodingExercise` + `SectionContent` | `difficulty`, `default_language`, `supported_languages` |
| `assignment` | `Assignment` + `SectionContent` | `instructions`, `passing_score` |

Each section must have at least one content item.

### 3. Complete quiz content

Every quiz must have:
- At least one `QuizQuestion`
- Each question must have at least one `QuizAnswer` with `is_correct = True`

This is enforced at submission time (not at quiz creation time).

### 4. Upload and transcode videos

For `lecture_type: video` lectures, the video must finish transcoding before submission:

1. Upload file → `VideoAsset` created with `status: uploading`
2. Celery runs FFmpeg → 5 HLS renditions produced
3. `VideoAsset.status` → `ready` (or `failed` after 3 retries)

Poll `GET /api/v1/courses/lectures/{lecture_id}/` and check `active_video_asset.status`. A course with any video in `uploading`, `processing`, or `failed` state cannot be submitted.

---

## Phase 3 — Submit for Review

### Endpoint

```
POST /api/v1/courses/{course_id}/submit/
```

### Permissions

```
IsAuthenticated + IsEmailVerified + IsVerifiedInstructor
```

The view also checks `instructors=request.user` at the query level — an instructor not assigned to the course receives a `404`.

### What happens

The view calls `course.transition_to('under_review')`.

`transition_to` first checks that the current status is `draft` (the only valid source for `under_review`). Then it calls `_validate_course_completeness()`.

### Completeness checks (`_validate_course_completeness`)

All checks run together — every problem is collected before the error is raised.

| Check | Failure message |
|-------|----------------|
| `title` is non-empty | `"title is required before submitting."` |
| `description` is non-empty | `"description is required before submitting."` |
| At least one section exists | `"Course must have at least one section."` |
| Every section has at least one content item | `"These sections have no content: <names>."` |
| All `VideoAsset` rows linked to this course have `status = ready` | `"N video(s) are still processing or failed."` |
| Every `Quiz` has at least one question | `"Incomplete quizzes: <quiz> has no questions."` |
| Every quiz question has at least one correct answer | `"Incomplete quizzes: <quiz> - Q<N> has no correct answer."` |

If any check fails, a `ValidationError` is raised with a dict keyed by problem area. The view returns:

```json
{
  "success": false,
  "message": "Course is not ready for submission.",
  "errors": {
    "sections": "Course must have at least one section.",
    "video_processing": "2 video(s) are still processing or failed. All videos must be ready before submission."
  }
}
```

HTTP status: `400`.

### Success response

```json
{
  "success": true,
  "message": "Course submitted for review.",
  "data": { "id": 1, "status": "under_review", ... }
}
```

The course is now locked from editing. `status` cannot be changed until an admin reviews it.

---

## Phase 4 — Admin Review

### Endpoint

```
POST /api/v1/courses/{course_id}/review/
```

### Permissions

```
IsAuthenticated + IsEmailVerified + IsAdminUser
```

`IsAdminUser` passes if `request.user.is_staff` or `request.user.user_type == 'admin'`.

### Request body

**To approve:**

```json
{ "action": "approve" }
```

**To reject:**

```json
{
  "action": "reject",
  "rejection_reason": "Lecture 3 audio quality is too low. Please re-record."
}
```

`action` must be `"approve"` or `"reject"`. Any other value returns `400`.

### What happens on approve

Calls `course.transition_to('published', reviewer=request.user)`.

- `status` → `published`
- `rejection_reason` is cleared
- Transition is logged: `Course <pk> (<slug>) transitioned to published by <admin email>`

Response:

```json
{
  "success": true,
  "message": "Course approved successfully.",
  "data": { "id": 1, "status": "published", ... }
}
```

### What happens on reject

Calls `course.transition_to('rejected', reviewer=request.user, rejection_reason="...")`.

- `rejection_reason` is required — omitting it raises `ValidationError({'rejection_reason': 'A reason is required when rejecting a course.'})` → `400`
- `status` → `rejected`
- `rejection_reason` is saved on the course

Response:

```json
{
  "success": true,
  "message": "Course rejected successfully.",
  "data": { "id": 1, "status": "rejected", "rejection_reason": "Lecture 3 audio quality is too low.", ... }
}
```

The instructor can read `rejection_reason` on the course detail endpoint.

---

## Phase 5 — Rework After Rejection

### Endpoint

```
POST /api/v1/courses/{course_id}/rework/
```

### Permissions

```
IsAuthenticated + IsEmailVerified + IsVerifiedInstructor
```

Again, `instructors=request.user` is enforced at the query level.

### What happens

Calls `course.transition_to('draft')`.

Only valid if current status is `rejected`. Any other status returns:

```json
{
  "success": false,
  "message": "Cannot transition from \"published\" to \"draft\". Allowed: archived."
}
```

On success, `status` → `draft`. The instructor can now edit the course and resubmit (back to Phase 2 / Phase 3).

Response:

```json
{
  "success": true,
  "message": "Course moved back to draft for reworking.",
  "data": { "id": 1, "status": "draft", ... }
}
```

---

## Phase 6 — Archive

### Endpoint

```
POST /api/v1/courses/{course_id}/archive/
```

### Permissions

```
IsAuthenticated + IsEmailVerified
```

The view then checks access manually:

- If `request.user.is_staff` or `user_type == 'admin'` → can archive any course
- Otherwise → `instructors=request.user` filter applies (instructor must be on the course)

### What happens

Calls `course.transition_to('archived')`.

Only valid if current status is `published`. From `archived`, the course can go back to `draft` if needed (the transition is in `VALID_TRANSITIONS`).

Response:

```json
{
  "success": true,
  "message": "Course archived successfully.",
  "data": { "id": 1, "status": "archived", ... }
}
```

---

## `transition_to` — full implementation summary

```python
def transition_to(self, new_status, reviewer=None, rejection_reason=''):
    # 1. Guard: is this a valid next state?
    allowed = self.VALID_TRANSITIONS.get(self.status, ())
    if new_status not in allowed:
        raise ValidationError(f'Cannot transition from "{self.status}" to "{new_status}". ...')

    # 2. Completeness check only on submit
    if new_status == 'under_review':
        self._validate_course_completeness()

    # 3. Rejection requires a reason
    if new_status == 'rejected' and not rejection_reason.strip():
        raise ValidationError({'rejection_reason': 'A reason is required when rejecting a course.'})

    # 4. Admin transitions require a reviewer object
    if new_status in ('published', 'rejected') and reviewer is None:
        raise ValidationError('A reviewer (admin) is required for this transition.')

    # 5. Apply
    self.status = new_status
    self.rejection_reason = rejection_reason.strip() if new_status == 'rejected' else ''
    self.save()

    logger.info('Course %s (%s) transitioned to %s by %s', ...)
```

All `ValidationError` exceptions bubble up to the view, which converts them to `400` responses with the appropriate shape.

---

## Permissions summary

| Action | Permission classes | Extra query guard |
|--------|-------------------|------------------|
| Create course | `IsAuthenticated`, `IsEmailVerified`, `IsVerifiedInstructor` | — |
| Edit course (PATCH) | `IsAuthenticated`, `IsEmailVerified`, `IsVerifiedInstructor` | `instructors=request.user` |
| Submit for review | `IsAuthenticated`, `IsEmailVerified`, `IsVerifiedInstructor` | `instructors=request.user` |
| Admin review | `IsAuthenticated`, `IsEmailVerified`, `IsAdminUser` | — |
| Rework | `IsAuthenticated`, `IsEmailVerified`, `IsVerifiedInstructor` | `instructors=request.user` |
| Archive | `IsAuthenticated`, `IsEmailVerified` | admin: any course; instructor: own course |

### Owner vs co-instructor distinction

All instructors in `course.instructors` share the same permission classes and query-level access. The distinction between the **owner** (`created_by`) and **co-instructors** is enforced inside the serializer, not at the permission layer:

| Sub-action | Owner | Co-instructor |
|------------|-------|---------------|
| Edit title, description, price, sections, lectures, quizzes, etc. | Yes | Yes |
| Modify instructor roster (`instructors` field in PATCH) | Yes | Silently ignored |
| Change partner institutions (`partner_institutions` field in PATCH) | Yes | Silently ignored |
| Submit for review, rework, archive, restore | Yes | Yes |

See `13-multi-instructor-collaboration.md` for the full enforcement details.

---

## Full end-to-end request sequence

```
# 1. Create
POST   /api/v1/courses/create/                            → status: draft

# 2. Build content
POST   /api/v1/courses/{id}/sections/create/
POST   /api/v1/courses/sections/{id}/contents/            → item_type: lecture|quiz|coding|assignment
POST   /api/v1/courses/quizzes/{id}/questions/
POST   /api/v1/courses/quiz-questions/{id}/answers/
# ... (add language configs, test cases, etc.)

# 3. Submit
POST   /api/v1/courses/{id}/submit/                       → status: under_review  (or 400 if incomplete)

# 4. Admin approves
POST   /api/v1/courses/{id}/review/   {"action":"approve"} → status: published

# --- OR admin rejects ---
POST   /api/v1/courses/{id}/review/   {"action":"reject","rejection_reason":"..."} → status: rejected

# 5. Instructor reworks
POST   /api/v1/courses/{id}/rework/                       → status: draft
# (fix content, then submit again)

# 6. Archive
POST   /api/v1/courses/{id}/archive/                      → status: archived
```

---

## Common error responses

### Invalid transition

```json
{
  "success": false,
  "message": "Cannot transition from \"under_review\" to \"under_review\". Allowed: published, rejected."
}
```

### Incomplete course on submit

```json
{
  "success": false,
  "message": "Course is not ready for submission.",
  "errors": {
    "description": "description is required before submitting.",
    "empty_sections": "These sections have no content: Introduction, Advanced Topics.",
    "video_processing": "1 video(s) are still processing or failed. All videos must be ready before submission.",
    "quizzes": "Incomplete quizzes: \"REST Basics Quiz\" - Q2 has no correct answer."
  }
}
```

### Missing rejection reason

```json
{
  "success": false,
  "message": "Review action failed.",
  "errors": {
    "rejection_reason": ["A reason is required when rejecting a course."]
  }
}
```

### Not assigned to course (submit/rework)

```json
{
  "detail": "No NidusCourse matches the given query."
}
```

HTTP status: `404` (ownership check is done at the query level, not as an explicit permission).
