# 33 — Two-Step Lecture Authoring

Creating a lesson and supplying its content are two separate actions.

| Step | Call | Result |
|---|---|---|
1. Details | `POST /api/v1/courses/sections/<id>/contents/` `{item_type: "lecture", title}` | Lecture row + curriculum slot. No payload. |
2. Content | `PATCH /api/v1/courses/lectures/<id>/` with `{lecture_type, article_content}` or multipart `{lecture_type: "video", video_file}` | Lecture becomes playable |

No new endpoint and **no migration** — step 2 is the lecture PATCH that already existed.

## Why

Before this, `LectureCreateUpdateSerializer.validate` refused to create a video
lecture without a file (`"Video lectures require a video file on creation."`).
An instructor had to have the finished file in hand before the row could exist,
so a curriculum skeleton could not be built ahead of the recording. It also
blocked any flow that drafts lessons first — including applying an AI-generated
outline.

## "Awaiting content" is derived, never stored

```python
# courses/all_models/content_models.py
Lecture.is_awaiting_content          # one row, reads a prefetch when present
lectures_awaiting_content(queryset)  # bulk: video lectures with no active VideoAsset
```

A lecture is awaiting content when `lecture_type='video'` **and** it has no
active `VideoAsset`. There is deliberately no `is_placeholder` column:

- A stored flag has a lifecycle, and every lifecycle drifts — a failed
  transcode would leave a row flagged "has content" with nothing to play.
  `VideoAsset.status` is already the authoritative record, so the derivation
  self-corrects.
- **Article lectures can never be awaiting content.** The DB check constraint
  `chk_lecture_payload_by_type` requires a non-blank `article_content` for that
  type, so an empty article lecture cannot exist in the first place.

`lecture_type` stays NOT NULL and keeps its `video` default. A step-1 lecture is
literally "a video lecture whose video hasn't arrived", which is a state the
schema already permitted — hence no migration. Step 2 may switch it to
`article`, and switching back to `video` clears the body server-side (the check
constraint requires it empty).

## The four consequences of an empty lecture

A lecture with nothing to play must not behave like content. Each of these is
enforced at exactly one place:

| Concern | Where | Behaviour |
|---|---|---|
Submission | `_validate_course_completeness` → `empty_lectures` | Blocks leaving `draft`, naming every offending lesson |
Learner curriculum | `learner_service.load_learner_curriculum` | Skipped for learners; **instructors still see it** in preview |
Progress | `enrollment_service.recalculate_progress` | Excluded from `total_items` |
Catalog | `curriculum_service.load_catalog_curriculum` | Pruned from the tree, and therefore from both item counts |

The progress exclusion is not cosmetic: an unplayable lecture in the denominator
makes 100% unreachable, so the enrollment never completes and the certificate
never issues.

`empty_lectures` and the existing `video_processing` check are **disjoint** —
the first is "no video was ever uploaded" (`.exclude(video_assets__is_active=True)`),
the second is "a video exists but isn't `ready`". A lesson is never reported twice.

`content_outline_stats()` is deliberately left alone: a section holding only
empty lectures still counts as non-empty there, because `empty_lectures` already
blocks that course and two errors for one problem is worse than one.

### It closes an existing hole

Nothing previously stopped a **video lecture with no video** from being
published — the old validator only checked that *existing* `VideoAsset` rows
were `ready`, so a lecture with zero assets passed. That was reachable before
this feature (delete the asset, or create the row outside the serializer) and is
now blocked.

### Drip courses

`LectureDetailAPIView` guards edits with `guard_editable(course, section=...)`,
which refuses to edit content already released to a cohort. A lecture awaiting
content is guarded **as if it were a create** (`_guard_section` passes `None`) —
nothing has been released, since learners never saw it. Without that carve-out a
lesson created before a section unlocked could never be filled in.

## Ownership: creator-inclusive, not instructors-only

Shipped in the same change because the flow is unusable without it.

A partner institution owns its courses through `created_by` and is **never**
added to `course.instructors` (only its experts are). The content endpoints
filtered on `course__instructors=request.user` and gated on `IsInstructorUser`,
so an institution account could create sections but got a 404 on every attempt
to put anything inside them — and its own course could then never be submitted.

The ownership shape is now one helper:

```python
# courses/utils.py
course_owner_q(user, path='course')   # Q(instructors) | Q(created_by)
owned_section_qs(user)                # CourseSection rows, already .distinct()
```

Always pair `course_owner_q` with `.distinct()` — the `instructors` M2M join
duplicates rows for a user who is both instructor and creator.

**On a queryset carrying an aggregate, filter by subquery instead.** The
assignment views annotate `Sum('questions__points')`; adding an ownership join
over a multi-valued relation would multiply that sum. They use
`section__in=_owned_section_ids(user)`, which compiles to `WHERE section_id IN
(SELECT ...)` and cannot multiply. `.distinct()` does **not** fix aggregate
multiplication — only avoiding the join does.

Gates moved from `IsInstructorUser` to `IsCourseCreator` across
`content_views.py`, `coding_views.py`, and `assignment_views.py`, matching the
rule CLAUDE.md already stated.

## Frontend

`is_awaiting_content` is exposed on `LectureSerializer` and on the lecture block
of `SectionContentSerializer`, so the builder never re-derives it. That list
endpoint now prefetches `video_assets` (it read them per row before — an N+1).

The builder shows a **No content** badge plus an **Add content** button on such
a row; `LessonModal` runs in two modes (`contentStep`), and the review tab
mirrors the `empty_lectures` check as a pre-flight so the blocker appears before
the 422 does. The preview drawer hides awaiting lessons — it previews the
learner's view, and learners don't see them.

## Tests

`courses/all_tests/test_two_step_lecture.py` — 22 tests: step 1 creates from a
title alone, step 2 by both branches, the article↔video switch (including that a
partial update never blanks an article body), the submission block and its
disjointness from `video_processing`, learner-hidden vs instructor-visible,
progress reaching 100%, catalog pruning, and institution ownership on the
content endpoints.
