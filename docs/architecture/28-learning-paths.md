# 28 — Learning Paths

A curated, ordered sequence of existing courses toward a named career goal (e.g. "AI/ML
Engineer"). An instructor or admin builds the path once; any learner can enroll in it and follow
the roadmap. This is the design for a feature that is currently **100% unbuilt** — no model, no
endpoint — and was previously listed only as deferred in `27-learner-dashboard.md` §10. This doc
is written before implementation, matching the convention used for scheduled courses and webinars.

Related: `27-learner-dashboard.md` (the honesty rules this design follows — no invented XP/AI
numbers), `12-enrollment.md`/course lifecycle docs (the `Enrollment` model this reuses read-only).

---

## 1. What problem does this solve?

The current `/dashboard/learner/learning-paths` frontend page is a fully hardcoded mock: a career
goal, a progress ring, six milestones, an "AI-optimized" badge, and an "Adjust with AI" button —
none of it backed by real data. This design replaces the mock with a real, curated roadmap while
being explicit about what is **not** built: there is no AI generation, no progress-estimation
model, and no per-learner path customization. Faking any of those would repeat the exact mistake
`27-learner-dashboard.md` warns against for `total_xp`.

## 2. The core idea: a path is an ordered list of existing courses

```
LearningPath "AI/ML Engineer"
    └── LearningPathMilestone #1 → NidusCourse "Programming Foundations"
    └── LearningPathMilestone #2 → NidusCourse "Data & Statistics"
    └── LearningPathMilestone #3 → NidusCourse "Machine Learning Core"
    └── ...
```

A path does **not** duplicate or wrap course content — each milestone is a pointer to an existing,
published `NidusCourse`. Building a path is arranging courses that already exist into an order,
nothing more. This mirrors how `CourseSchedule` wraps a course rather than copying it.

## 3. Data model

### 3.1 `LearningPath` (new file: `courses/all_models/learning_path_models.py`)

Inherits `AuthoredModel` (`created_by`/`last_edited_by`), same as `CourseSection`/`Webinar`.

| Field | Meaning |
|---|---|
| `title` | e.g. "AI/ML Engineer". |
| `slug` | Unique, for public/catalog-style URLs (`/learning-paths/<slug>/`). |
| `description` | Plain text, shown on the path's overview. |
| `career_goal` | Short label shown as the hero heading — may equal `title` but kept separate so the path can be titled differently from the goal it targets. |
| `skill_tags` | `JSONField(default=list)` — plain strings (e.g. `["Python", "MLOps"]`), same convention as `LearnerNote.tags`. Display-only, no filtering logic depends on it yet. |
| `status` | `TextChoices`: `draft`, `published`, `archived`. Only `published` paths are enrollable/listed. |
| `created_by` / `last_edited_by` | From `AuthoredModel`. |

No `partner_institution` FK — paths are platform-level curriculum curation, authored by
instructors or admins, not owned by a single institution. (If institution-scoped paths are wanted
later, add the FK then; don't speculatively add it now.)

### 3.2 `LearningPathMilestone`

| Field | Meaning |
|---|---|
| `path` | FK → `LearningPath`, `related_name='milestones'`. |
| `course` | FK → `NidusCourse`, `on_delete=PROTECT`. `PROTECT` (not `CASCADE`) — deleting a course that's a live milestone should fail loudly, not silently break a published path. Author must remove the milestone first. |
| `position` | Integer, defines order. Same ordering convention as `SectionContent.position`. |
| `title` | Optional override label (e.g. "Programming Foundations") — defaults to the course's own title if blank, but lets the path author phrase it as a stage name rather than reusing the course title verbatim. |

Unique constraint `(path, position)` and `(path, course)` — a course appears at most once per path,
and positions never collide within a path.

### 3.3 `LearningPathEnrollment`

| Field | Meaning |
|---|---|
| `user` | FK → learner. |
| `path` | FK → `LearningPath`. |
| `created_at` | When they joined the path. |

Unique `(user, path)`. This is intentionally the **only** new per-learner state. There is no
`LearningPathProgress` row and no per-milestone completion flag — see §4.

## 4. Progress is derived, never stored (the honesty rule)

Following the same principle as `total_xp`'s absence: a learner's progress through a path is
**100% derivable** from data that already exists (`Enrollment.completed_at`,
`Enrollment.is_active` on the milestone's course), so no second ledger is created that could drift
out of sync with the real enrollment state.

For a given `(learner, path)`:

```python
milestone_course_ids = [m.course_id for m in path.milestones.all()]
enrollments = {
    e.course_id: e
    for e in Enrollment.objects.filter(user=learner, course_id__in=milestone_course_ids)
}
```

Per milestone, in position order:

| Condition | Status |
|---|---|
| `enrollments[course_id].completed_at is not None` | `completed` |
| An active enrollment exists but isn't completed | `in_progress` |
| No enrollment, and every prior milestone is `completed` | `available` (next up) |
| No enrollment, and some prior milestone isn't `completed` | `locked` |

The first non-completed milestone in position order is the learner's "current" one — this is what
the frontend mock calls "in_progress" today. Overall path `progress_percent` is
`completed_count / total_milestones * 100` — one honest number, not an invented ring animation
target.

**No AI, no estimated-completion date.** The mock UI's "AI-optimized" badge, "Adjust with AI"
button, and "Est. completion Nov 2026" text are **dropped entirely** in the real version — none of
that is backed by anything. If AI-driven path generation becomes a real feature later, it needs its
own design (a generation service, a model change proposal flow) — this doc does not stub it.

## 5. Endpoints (`/api/v1/courses/learning-paths/`)

All learner-facing endpoints gated `[IsAuthenticated, IsEmailVerified, IsLearnerUser]` except the
public list/detail, which are `AllowAny` (mirrors the public catalog — a path is marketing content
before enrollment, same as a course).

| Method | Path | Audience | Purpose |
|---|---|---|---|
| GET | `learning-paths/` | AllowAny | List published paths (paginated). |
| GET | `learning-paths/<slug>/` | AllowAny | Path detail + milestone list (course titles/thumbnails), no progress. |
| GET | `learning-paths/<slug>/progress/` | Learner | Same detail, plus per-milestone derived status (§4) for the caller. 403 if the path isn't published (slug rule). |
| POST | `learning-paths/<slug>/enroll/` | Learner | Create `LearningPathEnrollment` (get-or-create, 201 first / 200 repeat — mirrors `add_to_wishlist`). |
| DELETE | `learning-paths/<slug>/enroll/` | Learner | Remove `LearningPathEnrollment`. Does **not** touch the learner's course enrollments — leaving a path never unenrolls them from milestone courses already joined. |
| GET | `my-learning-paths/` | Learner | The caller's enrolled paths with derived progress — powers the dashboard page's list. |

**Authoring** (instructor/admin), separate endpoint group, `IsCourseCreator`-gated (unverified
analog, matching how course authoring itself is gated — see *Permissions* in the root
`CLAUDE.md`):

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `learning-paths/manage/` | List own / create a path (draft). |
| GET/PATCH/DELETE | `learning-paths/manage/<int:pk>/` | Edit metadata, publish/archive (`status` field, simple direct set — no state-machine complexity needed for two meaningful transitions). Numeric ID → 404 on no-access. |
| POST/PATCH/DELETE | `learning-paths/manage/<int:pk>/milestones/[<int:milestone_id>/]` | Add/reorder/remove milestones. Reorder mirrors `reorder_section_content()` — accept a full ordered list of milestone IDs, reassign `position` in one transaction. |

`LearningPathError(message, http_status)` — same pattern as `ReviewError`/`ScheduleError`.

**403-vs-404:** slug-based learner endpoints (`<slug>/progress/`, `<slug>/enroll/`) → 403 on
unpublished/no-access (slugs are public once published, matching the catalog rule). Authoring
numeric-ID endpoints (`manage/<pk>/`) → 404 on not-own.

## 6. Query budget

| Endpoint | Queries | Notes |
|---|---|---|
| `learning-paths/` (list) | 2 | Paginated path query + `prefetch_related('milestones__course')` for the card's course-count/thumbnail preview. |
| `learning-paths/<slug>/progress/` | 3 | Path + milestones (`select_related('course')`) + one `Enrollment` filter keyed by the milestone course-id list (§4) — not per-milestone. |
| `my-learning-paths/` | 4 | `LearningPathEnrollment` list + prefetch milestones + the same batched `Enrollment` lookup, this time across all the learner's enrolled paths' milestone course ids in one query. |

No N+1: the enrollment-status lookup is always one query per request, keyed by the full course-id
list, never once per milestone or once per path.

## 7. What this explicitly does not do (not built, not faked)

- No AI-generated or AI-adjusted paths — "Adjust with AI" has no backing service.
- No estimated-completion date — no velocity model exists to predict one.
- No per-learner custom path editing (§ "Learner self-assembled" was considered and rejected in
  favor of curated paths — see the option this doc chose against).
- No path-level certificate — completing every milestone's course already issues each course's own
  certificate (`14-certificate-system.md`); a path-completion certificate would be a separate,
  later feature if wanted.
- No prerequisite/branching graph — milestones are a single linear sequence, matching the mock UI.
  A DAG-shaped path is out of scope unless a real need for branching appears.
