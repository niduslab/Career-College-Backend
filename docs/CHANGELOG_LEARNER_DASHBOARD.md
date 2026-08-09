# Changelog — Learner Dashboard, Wishlist, Notes & Streak

File-by-file record of every backend change made to ship the learner dashboard surface and the
completion fixes it uncovered. Grouped by phase. For the feature explained end-to-end (not
file-by-file), see `docs/architecture/27-learner-dashboard.md`.

**Why this work happened:** the frontend learner dashboard had 20 routes, only 8 of which talked to
the backend. The rest rendered hardcoded arrays. Four more pages were mocked even though a working
endpoint already existed. Phase 1 built the missing endpoints; phases 2 and 3 fixed three bugs that
building them exposed.

**Scope deliberately excluded:** XP / badges / leaderboards, learning paths, study groups,
consultancy booking, recommendations, AI assistant, live-room WebRTC. See `FEATURE_STATUS.md`.

---

## Phase 1 — Dashboard aggregates, certificates list, wishlist, notes

Nine new endpoints under `/api/v1/courses/`, all gated
`[IsAuthenticated, IsEmailVerified, IsLearnerUser]`.

### New files

| File | What it does |
|---|---|
| `courses/all_models/wishlist_models.py` | `Wishlist` — a thin `(user, course)` row with `uq_wishlist_user_course`. Lives in `courses` rather than a new app because the only wishlistable entity is `NidusCourse`, and a separate app would put a cross-app import on the catalog — the hottest path in the product. `clean()` restricts to learners and published courses. |
| `courses/all_models/note_models.py` | `LearnerNote` — a private note optionally anchored to a course, lecture and playback timestamp. `tags` is a GIN-indexed `JSONField` (every list field in this codebase is JSON; `tags__contains` compiles to jsonb `@>`, so an `ArrayField` would buy nothing and introduce the only Postgres-only field type). `color` is a `TextChoices` enum, not free-form hex — a hex string echoed into the DOM is a style-injection surface. `course`/`lecture` are `SET_NULL`: notes are the learner's own work and a course teardown must not destroy them. Two `CheckConstraint`s make "a timestamp requires a lecture" and "body is non-empty" durable. |
| `courses/services/dashboard_service.py` | The four read-only aggregates: `get_learner_summary`, `get_learner_activity_feed`, `get_learner_upcoming`, `get_continue_target`. Owns no model — every number comes from tables that already existed. See §"Design decisions" below for why `total_xp` is absent and how the activity feed is merged. |
| `courses/services/wishlist_service.py` | `get_learner_wishlist`, `add_to_wishlist` (idempotent `get_or_create`, so a double-tapped heart is never an error), `remove_from_wishlist`, and `get_wishlisted_course_ids` — the helper that makes the catalog's `is_wishlisted` flag cost one query per page instead of one per row. |
| `courses/services/note_service.py` | Note CRUD plus `_validate_note_params`, which collects **every** bad filter param before raising so two mistakes yield one 400 listing both. `NoteError(message, http_status)` mirrors `ReviewError`. `_resolve_targets` derives the course from `lecture_id` when only the lecture is given. |
| `courses/all_serializers/dashboard_serializers.py` | Plain `Serializer` classes over the aggregate dicts. Serializing rather than returning raw dicts gives a schema-stable contract and rounds floats in one place. |
| `courses/all_serializers/wishlist_serializers.py` | `WishlistItemSerializer` = `{id, course, created_at}`, mirroring `EnrollmentSerializer` so the frontend renders wishlist, catalog and my-courses with the same card. |
| `courses/all_serializers/note_serializers.py` | `LearnerNoteReadSerializer` (minimal nested course/lecture — a note list must not drag a full catalog card per row) and `LearnerNoteWriteSerializer`, a plain `Serializer` so FK resolution stays in the service and `partial=True` gives clean PATCH semantics. |
| `courses/all_views/dashboard_views.py` | The four aggregate endpoints. Each wraps its service call and maps `ValidationError` → 400, unexpected → logged 500. |
| `courses/all_views/wishlist_views.py` | `WishlistListView` and `CourseWishlistView` (POST/DELETE on one path, same shape as `MyReviewView`). POST returns **201 first, 200 on repeat**. |
| `courses/all_views/note_views.py` | `LearnerNoteListCreateView` and `LearnerNoteDetailView`. Detail returns **404, never 403**, for another learner's note — the project's policy for non-enumerable numeric IDs. |
| `courses/migrations/0027_learnernote_wishlist.py` | Creates both tables. No data migration, no backfill — both are new. |
| `courses/migrations/0028_learner_activity_indexes.py` | Adds `(user, -submitted_at)` to the three submission tables and `(user, -created_at)` to `Enrollment`. Their existing composites have an unused middle column (`quiz`/`assignment`/`exercise`), so the activity feed would otherwise scan on the leading `user` column and then sort. Kept separate from `0027` so it can be dropped independently, or converted to `AddIndexConcurrently` (`atomic = False`) if those tables are already large in production. |
| `courses/all_tests/test_learner_dashboard.py` | Covers all four aggregates: permission triple, zero-data learner, exact counts, `total_xp` absence, feed ordering across mixed sources, window cap, upcoming horizon clamping, dual-enrollment de-duplication, resume-target advancement. |
| `courses/all_tests/test_wishlist.py` | Idempotent add, DB constraint, 404s, scoping, and the catalog-flag query budget (anonymous costs nothing extra; authenticated costs exactly +1). |
| `courses/all_tests/test_learner_notes.py` | CRUD, anchor derivation, constraint enforcement at both serializer and DB level, tag normalisation, filter combinations, and the 404-not-403 policy on all three verbs. |
| `courses/all_tests/test_my_certificates.py` | Ownership scoping, ordering, URL resolution, and the snapshot-vs-live title distinction. |
| `docs/architecture/27-learner-dashboard.md` | The end-to-end design doc. |

### Modified files

| File | What changed |
|---|---|
| `courses/all_models/__init__.py` | Star-imports `wishlist_models` and `note_models` so both are reachable via `courses.models`. |
| `courses/all_models/assessment_models.py` | Added a `(user, -submitted_at)` index to `QuizAttempt`, `AssignmentSubmission` and `CodingSubmission` — see migration `0028` above for why the existing composites don't serve the activity feed. |
| `courses/all_models/enrollment_models.py` | Added `idx_enroll_user_created` — the feed orders enrollments by creation, so `idx_enroll_user_active_last` doesn't apply. |
| `courses/all_serializers/enrollment_serializers.py` | Added `_WishlistFlagMixin` and `is_wishlisted` to both catalog serializers. The flag reads a pre-computed id set from serializer context and **never queries**; absent context yields `False`, so anonymous callers and the nested card inside `EnrollmentSerializer` are unaffected. Chosen over an `Exists()` annotation because `filter_catalog_courses` already stacks `.distinct()` and `.annotate(Count(...))`, and an annotation would evaluate across the full matched set before the `LIMIT` while coupling a pure queryset builder to request auth state. |
| `courses/all_serializers/certificate_serializers.py` | Added `LearnerCertificateListSerializer`. Returns both `course_title` (the snapshot frozen at issue — the record of what was awarded) and a live nested `course`; they legitimately differ after a rename. URLs are **relative**, matching the catalog's relative thumbnails. |
| `courses/services/certificate_service.py` | Added `get_learner_certificates`. The explicit `order_by` is required: `Certificate.Meta` declares none, and paginating an unordered queryset warns and can skip or duplicate rows across pages. |
| `courses/all_views/certificate_views.py` | Added `MyCertificateListView`. Two queries — `select_related('enrollment__course')` covers the nested card. |
| `courses/all_views/enrollment_views.py` | Both catalog views now build `wishlisted_course_ids` context **after** pagination, so the flag costs one `IN` lookup per page. |
| `courses/all_serializers/__init__.py`, `courses/all_views/__init__.py`, `courses/services/__init__.py`, `courses/views.py` | Star-imports / re-exports for the new modules, per the app's exploded-module convention. |
| `courses/urls.py` | Adds the 9 routes. **Ordering note in the file:** they are literal-prefixed and safe against the `<slug:slug>/…` routes (which all pin a fixed second segment), but must not be nested under `my-courses/` — `my-courses/<slug:slug>/` would swallow `my-courses/certificates/` and resolve it with `slug='certificates'`. Hence top-level `my-certificates/`. |
| `courses/admin.py` | Registers `Wishlist` and `LearnerNote`. |
| `CLAUDE.md`, `FEATURE_STATUS.md` | Reference section and built/not-built updates. |

### Design decisions worth keeping

**`total_xp` is absent from the summary — not zero, absent.** It is not derivable from any table.
Any invented formula is retroactively unstable (changing the weights silently rewrites every
learner's history) and cannot back an XP timeline or leaderboard. That needs an append-only
`LearnerXpEvent` ledger. The frontend renders four tiles instead of five.

**`total_learning_seconds` is approximate and says so in the docstring.**
`upsert_watch_progress` stores `watched_seconds` as the *furthest playback cursor*, clamped to the
video duration — not accumulated watch time. Re-watching does not increase it.

**The activity feed is a Python k-way merge, not `QuerySet.union()`.** Six per-source querysets,
each `select_related` and capped at `ACTIVITY_WINDOW = 200`, merged with `heapq.merge`. Each source
is individually sorted, so merging their capped heads *is* the true top-K of the union. `.union()`
was rejected because it forces six padded `.values()` shapes, kills `select_related`, and blocks
post-filtering; raw SQL was rejected because it abandons the ORM-only service convention and
hand-rolls pagination. Documented consequence: paginated `count` is the window size, not lifetime
activity.

**`learner/upcoming/` is not paginated.** The list is inherently tiny and a cursor across four
ascending heterogeneous sources buys nothing. Bounded by `?days=` and `?limit=` instead.

**`learner/continue/` reuses `get_learner_enrollments` + `load_learner_curriculum`.** Both already
encode the ordering, completion lookup and cohort/drip lock rules. A new traversal would duplicate
and eventually diverge from the lock semantics. Returns **200 with `data: null`** when there is no
active enrollment, not 404.

---

## Phase 2 — `LearnerActivityDay`: making the day streak real

**Why:** the streak originally unioned four consumption tables. Three were sound (`auto_now_add`
submission timestamps). The fourth, `WatchProgress.last_watched_at`, is `auto_now` — it holds only
the most recent touch per `(user, lecture)`. A learner re-watching one lecture daily for 30 days
showed a **1-day** streak, and re-opening an old lecture *erased* the historical date it carried.
Two more cases recorded nothing at all: re-reading a completed article (the UI hides its
"mark as complete" button once done) and running a coding exercise without submitting.

### New files

| File | What it does |
|---|---|
| `courses/all_models/activity_models.py` | `LearnerActivityDay(user, activity_date)`, unique per pair. **Day-granular, not event-granular**: a streak only asks "did anything happen on date D", and the video player POSTs progress every few seconds — one row per event would be thousands per lecture. The unique constraint collapses it to one row, so the streak read is an index-only scan needing no `DISTINCT`. `activity_date` is *stored*, not derived, so a later `TIME_ZONE` change cannot retroactively shift historical days. Explicitly **not** an XP ledger — XP needs one row per scoring event with a points value, the opposite de-duplication rule. |
| `courses/services/activity_service.py` | `record_learner_activity(user)` — the only writer, idempotent per day. **Never raises**: it is bookkeeping hung off the side of real requests, and a failure must not turn a working lecture fetch into a 500. A lost row costs at most one day, and the next action that day re-records it. Skips non-learners so instructor preview cannot build a streak. Plus `get_activity_dates` for the streak read. |
| `courses/migrations/0029_learner_activity_day.py` | Creates the table. |
| `courses/migrations/0030_backfill_learner_activity_days.py` | Seeds it from the four old sources so existing learners keep their history. Best-effort by nature: the three `auto_now_add` sources recover exactly, but every watch date `last_watched_at` overwrote is already gone — which is the whole reason the table exists. Reversible (the inverse empties the table); `ignore_conflicts=True` makes it safe to re-run after a partial failure. |
| `courses/all_tests/test_learner_activity_day.py` | Idempotency, non-learner skip, anonymous tolerance, DB constraint, and one regression per closed gap — including that instructor preview and plain browsing record nothing. |

### Modified files

| File | What changed |
|---|---|
| `courses/services/learner_service.py` | Calls `record_learner_activity(user)` from nine places: the four consumption loaders (inside their existing `if not is_instructor:` branches, so preview is excluded for free), `upsert_watch_progress`, the three submit paths, and `run_coding_exercise`. Opening content counts deliberately — that is what makes re-reading a finished article register. |
| `courses/services/dashboard_service.py` | `_compute_day_streak` now reads the ledger; the four-source `_activity_dates` helper and the `TruncDate` import are gone. `day_streak_is_approximate` flipped to **`False`** — kept in the response rather than dropped so it can flip back if per-user timezones ever land (days still bucket in the platform-wide `TIME_ZONE`, reported as `day_streak_timezone`). Summary dropped from 7 queries to **4**. |
| `courses/all_models/__init__.py`, `courses/services/__init__.py` | Exports. |
| `courses/admin.py` | Registers `LearnerActivityDay` read-only (`has_add_permission`/`has_change_permission` return `False`) — the table is append-only and written only by the service. |
| `courses/all_tests/test_learner_dashboard.py` | Streak tests rewritten against the ledger, plus window-boundary, caller-scoping and an `assertNumQueries(4)` guard so the query count cannot creep back. |

---

## Phase 3 — Three completion bugs

All three predate this work. The new `courses_completed` tile is what made them visible.

### Bug 1 — `completed_at` was not sticky

`recalculate_progress` used to clear it whenever progress fell back below 100:

```python
elif progress < 100 and enrollment.completed_at is not None:
    enrollment.completed_at = None      # removed
```

`total_items` counts every `SectionContent` row, so an instructor adding one lecture silently
un-completed everyone who had already finished — on their next watch tick or submission the course
dropped out of the My Courses "Completed" tab and out of `courses_completed`. Meanwhile
`issue_certificate` is `get_or_create` and is never revoked, so the two records contradicted each
other: a learner held a certificate for a course the platform no longer called complete.

Completion is now permanent. The learner did finish the course as it existed then, and that is what
`completed_at` records; `progress_percent` still moves, so later additions show as an unfinished
remainder.

### Bug 2 — My Courses only ever loaded one page

The frontend called `/my-courses/` with no `page_size`, took `data.results`, then filtered and
paginated *client-side* — so enrollment 11 onward was unreachable in every tab and the tab counts
were wrong. The list orders by `last_accessed_at DESC NULLS LAST`, so a finished course the learner
had stopped opening was the first thing to fall off the end. The same silent cap affected three
other callers: the catalog's enrolled flags, the Q&A course selector, and the note editor's course
picker.

### Bug 3 — a completed course the learner unenrolled from disappeared

Found by inspecting a real account: the enrollment had `progress=100`, `completed_at` set, a valid
certificate — and `is_active=False`. `unenroll_learner` is a soft revoke that preserves progress and
`completed_at` and never revokes the certificate, but both the list and its counts filtered
`is_active=True`. So My Courses reported **0 completed** while the summary (which has never filtered
on `is_active`) reported **1**, and the certificate had no course to open from.

### Modified files

| File | What changed |
|---|---|
| `courses/services/enrollment_service.py` | **Bug 1:** removed the `completed_at` reset branch, with a comment explaining why it must not come back. **Bug 2:** added `ENROLLMENT_STATUS_OPTIONS`, a `status` parameter on `get_learner_enrollments` (validated; unknown → `ValidationError`), and `get_learner_enrollment_status_counts` — one aggregate giving exact tab counts, which must be server-side because they describe the whole enrollment set. **Bug 3:** added `_learner_enrollment_scope`, which widens the base filter to `Q(is_active=True) \| Q(completed_at__isnull=False)` behind an **opt-in** `include_unenrolled_completed` flag. Opt-in and not the default because `get_continue_target` shares this queryset and must never resume a course the learner has lost access to. `in_progress` pins `is_active=True` on both filter and count, so an unenrolled completed course appears under **Completed** only. |
| `courses/all_views/enrollment_views.py` | `MyCoursesListView` accepts `?status=all\|in_progress\|completed` (bad value → 400) and returns a `status_counts` block beside the paginator keys. Passes `include_unenrolled_completed=True`. |
| `courses/services/dashboard_service.py` | Comment on `get_continue_target` recording why it keeps the default active-only scope. |
| `courses/services/__init__.py` | Exports `ENROLLMENT_STATUS_OPTIONS` and `get_learner_enrollment_status_counts`. |
| `courses/all_tests/test_enrollment.py` | `test_adding_content_after_completion_does_not_uncomplete` for Bug 1; a `MyCoursesStatusFilterTests` class for Bugs 2 and 3 covering the status filter, count exactness under pagination, the unenrolled-completed course appearing, it appearing under Completed *only*, the counts agreeing with the dashboard summary, and the resume target never selecting it. |
| `CLAUDE.md`, `docs/architecture/27-learner-dashboard.md` | The sticky-completion and unenrolled-completed invariants, written up so they are not "fixed" back. |

---

## Verification

| Check | Result |
|---|---|
| `python manage.py test` | **871 tests, OK** |
| `python manage.py check` | no issues |
| `python manage.py makemigrations --check --dry-run` | no changes detected |
| Migration round-trip `head → 0026 → head` | clean |

Endpoint behaviour was verified by the test suite and by inspecting a real learner account against
the live dev database. No manual HTTP smoke test was run against a running server.

---

## Files NOT changed by this work

`README.md`, `career_college_backend/settings.py` and `docs/deployment/AWS_DEPLOYMENT_ARCHITECTURE.md`
carry recent modification timestamps but were edited outside this effort. Nothing here touched
settings, Celery configuration, or deployment.
