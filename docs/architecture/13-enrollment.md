# 13 — Learner Enrollment

## Overview

The enrollment system connects learners to published courses. It serves as the access-control gateway: without an active enrollment, a learner can browse the public catalog but cannot access lectures, quizzes, assignments, or coding exercises.

## Data Model

### Enrollment

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK → User | The learner (must have `user_type='learner'`) |
| `course` | FK → NidusCourse | Must be a published course |
| `enrollment_type` | CharField | `free`, `paid`, or `admin_granted` |
| `is_active` | BooleanField | `False` on unenroll (soft revoke, progress preserved) |
| `progress_percent` | PositiveIntegerField | Denormalized 0–100 completion percentage |
| `completed_at` | DateTimeField (null) | Set when `progress_percent` first reaches 100 |
| `last_accessed_at` | DateTimeField (null) | Updated each time the learner opens course content |
| `created_at` / `updated_at` | auto timestamps | Via `TimestampedModel` |

**Constraints:** `UniqueConstraint(user, course)` — one enrollment per learner per course (including inactive).

**Indexes:** `(user, is_active, -last_accessed_at)` for "My Courses" dashboard, `(course, is_active)` for course enrollment counts.

### Relationship to Existing Models

- `Enrollment.user` → `authentication.User` (learner)
- `Enrollment.course` → `courses.NidusCourse` (published)
- `WatchProgress` already tracks per-lecture completion — the enrollment service uses this to compute `progress_percent`.

## API Endpoints

### Public Catalog (no authentication required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/courses/catalog/` | Paginated published courses. Filters: `?category=`, `?level=`, `?language=`, `?search=` |
| GET | `/api/v1/courses/catalog/{slug}/` | Course detail with metadata, objectives, prerequisites, audiences. No curriculum content. |

### Enrollment Actions (authenticated learner)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/courses/{slug}/enroll/` | Create an enrollment. Reactivates if previously unenrolled. |
| POST | `/api/v1/courses/{slug}/unenroll/` | Soft-deactivate enrollment. Progress preserved. |

### My Courses Dashboard (authenticated learner)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/courses/my-courses/` | Paginated active enrollments with progress. |
| GET | `/api/v1/courses/my-courses/{slug}/` | Single enrollment detail with full course metadata. |

## Enrollment Flow

1. **Browse** — Anyone visits `/catalog/` and `/catalog/{slug}/` to discover courses.
2. **Enroll** — Authenticated learner with verified email calls `POST /{slug}/enroll/`.
3. **Access** — Enrolled learner accesses course content via enrolled-only endpoints (to be built: learner curriculum view, learner lecture view).
4. **Progress** — As the learner completes lectures (via `WatchProgress`), the enrollment service recalculates `progress_percent`.
5. **Complete** — When `progress_percent` reaches 100, `completed_at` is set.
6. **Unenroll** — Learner can soft-deactivate via `POST /{slug}/unenroll/`. Progress is preserved for re-enrollment.

## Re-enrollment

If a learner unenrolls and later re-enrolls, the existing `Enrollment` record is reactivated (not duplicated). Their prior `progress_percent` and `WatchProgress` records remain intact.

## Progress Calculation

```
progress_percent = (completed_content_items / total_content_items) * 100
```

Currently counts completed lectures via `WatchProgress.is_completed`. As quiz-taking and assignment-submission features are built, their completion will be added to the numerator.

The `recalculate_progress()` service function is the single entry point for this calculation. It should be called after any content-completion event (e.g., marking a lecture as watched).

## Permissions

| Permission Class | Location | Purpose |
|-----------------|----------|---------|
| `IsLearnerUser` | `core/permissions.py` | Gates enrollment and my-courses endpoints to learner accounts only. |

Catalog endpoints use `AllowAny` (public).

## Design Decisions

**Why denormalize `progress_percent`?** The "My Courses" dashboard shows progress for every enrolled course. Computing it on-the-fly would require JOINs across `Enrollment → NidusCourse → CourseSection → SectionContent → WatchProgress` for each course. A denormalized field makes the dashboard query a simple `SELECT` on the `enrollments` table.

**Why soft-delete on unenroll?** Preserving the enrollment record (with `is_active=False`) retains the learner's progress and allows seamless re-enrollment without data loss. The unique constraint on `(user, course)` covers both active and inactive enrollments.

**Why no payment integration yet?** The `enrollment_type` field future-proofs the schema. Until payment integration is added, all enrollments are created with `enrollment_type='free'`, even if a course has a non-zero `price`. When a `payments` app is built, it will create enrollments with `enrollment_type='paid'` upon successful checkout. The enrollment system itself doesn't need to change.

## Files Created / Modified

| File | Change |
|------|--------|
| `courses/all_models/enrollment_models.py` | New — `Enrollment` model |
| `courses/all_models/__init__.py` | Added `enrollment_models` import |
| `courses/services/enrollment_service.py` | New — enroll, unenroll, progress, catalog queries |
| `courses/services/__init__.py` | Added enrollment service re-exports |
| `courses/all_serializers/enrollment_serializers.py` | New — catalog + enrollment serializers |
| `courses/all_serializers/__init__.py` | Added `enrollment_serializers` import |
| `courses/all_views/enrollment_views.py` | New — catalog, enroll, my-courses views |
| `courses/all_views/__init__.py` | Added enrollment view re-exports |
| `courses/views.py` | Added enrollment view re-exports |
| `courses/urls.py` | Added 6 new URL patterns |
| `core/permissions.py` | Added `IsLearnerUser` permission class |

## Future Extensions

- **Learner curriculum view** — `GET /my-courses/{slug}/curriculum/` showing sections + content items with per-item completion status.
- **Learner lecture view** — `GET /my-courses/lectures/{id}/` with streaming URL (enrolled-only access).
- **Watch progress endpoint** — `POST /my-courses/lectures/{id}/progress/` to update seconds watched and trigger progress recalculation.
- **Payment integration** — A `payments` app that creates `enrollment_type='paid'` enrollments on checkout.
- **Certificates** — PDF generation triggered when `completed_at` is set.
- **Course reviews/ratings** — Learner feedback on completed courses.
