# 27 — Learner Dashboard, Certificates List, Wishlist & Notes

The learner-facing surface that sits *above* course consumption: the dashboard
home, the certificates list, saved courses, and private notes. Everything here
lives in the `courses` app under `/api/v1/courses/`.

Related: `14-certificate-system.md` (issuance), `22-scheduled-courses.md`
(cohorts and drip release), `26-discussion-qa.md` (course Q&A).

---

## 1. What is here, and why

Nine endpoints, all gated `[IsAuthenticated, IsEmailVerified, IsLearnerUser]`:

| Method | Path (under `/api/v1/courses/`) | Purpose |
|---|---|---|
| GET | `learner/dashboard/summary/` | KPI tiles |
| GET | `learner/activity/` | recent-activity feed |
| GET | `learner/upcoming/` | upcoming cohort / drip / webinar dates |
| GET | `learner/continue/` | resume target + next lecture |
| GET | `my-certificates/` | the learner's certificates |
| GET | `wishlist/` | saved courses |
| POST / DELETE | `<slug>/wishlist/` | save / unsave a course |
| GET / POST | `notes/` | list + create private notes |
| GET / PATCH / DELETE | `notes/<pk>/` | one note |

Two new models: `Wishlist` and `LearnerNote`. The four dashboard aggregates
own **no** model — every number is computed from tables that already exist.

---

## 2. Honesty rules baked into the summary endpoint

`GET learner/dashboard/summary/` is the one place where it would have been easy
to invent numbers. It deliberately does not.

**`total_xp` is absent — not zero, absent.** XP is not derivable from any
existing table. Any formula (10 per lecture, 5 per quiz…) is a product
decision that would be *retroactively unstable*: changing the weights silently
rewrites every learner's history. It also cannot back the features that always
follow an XP tile — an XP timeline, a leaderboard, a "+50 XP" toast — because
there are no XP events to show. The correct fix is an append-only
`LearnerXpEvent(user, event_type, points, source, awarded_at)` ledger written
by the same signals that already fire on completion. Until that exists, the key
does not appear and the frontend renders four tiles instead of five.

**`total_learning_seconds` is approximate, and says so in the docstring.**
`upsert_watch_progress` (`learner_service.py`) stores `watched_seconds` as the
*furthest playback cursor position*, clamped to the video duration — not
accumulated watch time. Re-watching a lecture does not increase it. So the sum
is "total distinct video content reached": it under-counts re-watching and
over-counts scrubbing to the end. It is the only honest number available from
existing data.

Scope note: the sum includes `WatchProgress` rows for courses the learner later
unenrolled from, because `unenroll_learner` is an explicit soft-revoke that
preserves progress. Hours already learned should not evaporate.

**`day_streak` reads a purpose-built table.** See §2a — it is exact going
forward, so `day_streak_is_approximate` is `false`. The field is kept in the
response rather than dropped, because one caveat remains: days are bucketed in
the platform-wide `settings.TIME_ZONE` (reported as `day_streak_timezone`),
and there is no per-user timezone field, so evening study in UTC+6 lands on
the previous UTC day. If per-user timezones ever land, the flag can flip back
without a contract change.

Grace rule: the walk starts at today if present, else yesterday. A streak does
not read 0 at 00:01 before the learner has had a chance to study.

`STREAK_WINDOW_DAYS = 120` bounds the scan to at most 120 rows (one per active
day). **To drop the streak entirely**, delete the `_compute_day_streak` call
and its three keys — the other seven are unaffected.

Query budget: **4, constant.**

---

## 2a. `LearnerActivityDay` — why the streak has its own table

The streak originally unioned four consumption tables. Three were sound;
the fourth broke it.

| Source | Column | Sound? |
|---|---|---|
| `QuizAttempt` | `submitted_at` | ✅ `auto_now_add` — immutable, one row per attempt |
| `AssignmentSubmission` | `submitted_at` | ✅ same |
| `CodingSubmission` | `submitted_at` | ✅ same |
| `WatchProgress` | `last_watched_at` | ❌ `auto_now` — **overwritten, not event-sourced** |

`WatchProgress` holds only the most recent touch per `(user, lecture)`. A
learner re-watching one lecture daily for 30 days showed a **1-day** streak,
and re-opening an old lecture *erased* the historical date it carried. Two
further blind spots had no source at all: **re-reading a completed article**
(the UI hides its "mark as complete" button once done, so nothing wrote) and
**running a coding exercise without submitting** (a Run persists nothing).

`LearnerActivityDay(user, activity_date)` records the fact directly.

**Day-granular, not event-granular.** A streak only ever asks "did anything
happen on date D", and the video player POSTs progress every few seconds —
one row per event would be thousands of rows per lecture. The
`uq_activity_day_user_date` unique constraint collapses all of it to one row,
so `get_activity_dates` is an index-only scan needing no `DISTINCT`.

**`activity_date` is stored, not derived.** Freezing the local date at write
time means a later `TIME_ZONE` change cannot retroactively shift historical
days, and it turns the old `DISTINCT TruncDate(...)` hash-aggregate into a
plain indexed range scan.

**This is not an XP ledger, and must not become one.** XP needs one row per
scoring event carrying a points value; the streak needs at most one row per
day. Opposite de-duplication rules — two models. `total_xp` stays absent until
that second model exists.

### What counts

`record_learner_activity(user)` (`courses/services/activity_service.py`) is the
only writer. It is called from `learner_service.py`, never from a view, and
never for instructor preview:

| Path | Counts |
|---|---|
| Opening a lecture, quiz, assignment or coding exercise | ✅ |
| Posting watch progress (video tick **or** article mark-complete) | ✅ |
| Submitting a quiz, assignment or coding exercise | ✅ |
| Running a coding exercise without submitting | ✅ |
| Instructor previewing their own course | ❌ — guarded by the `is_instructor` branch in each loader |
| Dashboard, catalog, my-courses, notes, Q&A | ❌ — browsing is not studying |

Opening content counts deliberately: it is what makes re-reading a finished
article register. Enrollment is excluded — enrolling is not studying.

The function **never raises**. It is bookkeeping hung off the side of real
requests, and a failure here must not turn a working lecture fetch into a 500.
A lost row costs at most one day, and the next action that day re-records it.

### Backfill

Migration `0030` seeds the table from the four old sources so existing
learners keep their history. Best-effort by nature: the three `auto_now_add`
sources recover exactly, but every watch date `last_watched_at` overwrote is
already gone and cannot be reconstructed — which is the whole reason the table
exists. Reversible: the inverse empties the table.

---

## 3. Activity feed — why a Python k-way merge

`get_learner_activity_feed` fetches the top-`ACTIVITY_WINDOW` rows from each of
six sources, each already `select_related` and ordered DESC by its own
timestamp, then merges them with `heapq.merge`.

Correctness is not a heuristic: each source is individually sorted, so the
global maximum is always at the head of some source — the merge of capped heads
*is* the true top-K of the union.

**Rejected alternatives:**

- **`QuerySet.union()`** — requires every branch to project an identical column
  list, so six heterogeneous shapes need `.values()` padded with
  `Value(None, output_field=…)` casts. Adding a field means editing six places.
  The result is dicts, so `select_related` is unavailable and every title needs
  a hydration pass or an N+1. Django also forbids most post-`union()` filtering
  and only allows `order_by` on aliases present in every branch — and the sort
  key differs per source anyway.
- **Raw SQL `UNION ALL`** — best raw performance, but abandons the ORM-only
  service convention the codebase holds everywhere, needs hand-rolled
  `LIMIT/OFFSET` outside `StandardResultsSetPagination`, and would silently
  bypass the row-level `user` filter if edited carelessly.

**The documented trade-off:** `count` in the paginated envelope is the size of
the capped window (≤200), **not** lifetime activity. That is the price of the
cap, and the cap is what makes deep pagination impossible to abuse. A recent-
activity feed is a dashboard widget, not an archive.

Query budget: **6, independent of page depth and dataset size.**

Migration `0028` adds `(user, -submitted_at)` indexes on the three submission
tables and `(user, -created_at)` on `Enrollment`. Their existing composites
have an unused middle column (`quiz`, `assignment`, `exercise`), so Postgres
would scan on the leading `user` column and then sort. Kept as a separate
migration so it can be dropped, or converted to `AddIndexConcurrently` with
`atomic = False` if those tables are already large in production.

---

## 4. Upcoming and continue

**`GET learner/upcoming/` is not paginated.** An enrolled learner has a handful
of cohorts and registrations; paginating an ascending union across four sources
would need a cursor per source for no benefit. Bounded by `?days=` (default 30,
max 365) and `?limit=` (default 20, max 50) instead. Four indexed queries.

The `section_unlocks` source **must** keep its `.distinct()`: a learner may
hold both a self-paced and a cohort enrollment for the same course
(`learner_service.py`), which duplicates every section through the join.

**`GET learner/continue/` reuses two existing services rather than
reimplementing them:**

- `get_learner_enrollments(user)` (`enrollment_service.py`) already orders by
  `last_accessed_at DESC NULLS LAST` with the joins and prefetch in place, so
  `.first()` *is* the resume-course selector.
- `load_learner_curriculum(...)` (`learner_service.py`) already returns ordered
  sections and items with per-lecture `is_completed` from one batched
  `WatchProgress` query and per-section `is_locked` from the cohort and drip
  gates. The service walks that payload.

Writing a new traversal would duplicate — and eventually diverge from — the
lock semantics. There is no pre-existing "next lecture" helper;
`courses/selectors.py` is 16 lines of instructor course-queryset helpers.

Empty state: **200 with `data: null`**, not 404. A learner with no enrollments
is not an error, and the frontend renders a browse-the-catalog CTA without
special-casing a status code.

| Case | Response |
|---|---|
| No active enrollment | `data: null` |
| All lectures complete | `next_lecture: null`, `is_course_complete: true` |
| Everything left is locked | `next_lecture: null`, `locked_until: <earliest unlocks_at>` |

---

## 5. Wishlist

Model `Wishlist` (`db_table='course_wishlists'`) lives in **`courses`**, not a
new app. The only wishlistable entity is `NidusCourse`; a separate app would
put a cross-app import on the hottest path in the product — the public catalog,
which resolves `is_wishlisted` on every card. `courses` already owns every
learner↔course relation (`Enrollment`, `WatchProgress`, `Certificate`,
`CourseReview`, `CourseQuestion`). A future webinar wishlist mirrors this
pattern in ~30 lines with no data migration; a generic app would need a
`GenericForeignKey`, losing FK integrity and turning the catalog flag into a
content-type join.

`add_to_wishlist` is `get_or_create` — **201 on first add, 200 on repeat**. A
double-tapped heart must never be an error. An `IntegrityError` catch handles
the concurrent double-POST that loses the race against
`uq_wishlist_user_course`. `clean()` is mirrored explicitly in the service
because `Model.objects.create()` does not invoke it.

### `is_wishlisted` on catalog cards

There was no `is_enrolled` flag to copy (`grep is_enrolled` over the repo
returns zero). The nearest precedent is `CourseReviewListView`, whose comment
states the principle: *one extra round-trip per page, not per row.*

Mechanism: `get_wishlisted_course_ids(user, ids)` returns a set, computed
**after** pagination, passed to the serializer as `wishlisted_course_ids`
context. `_WishlistFlagMixin.get_is_wishlisted` reads that set and returns
`False` when the key is absent — so anonymous callers and the nested card
inside `EnrollmentSerializer` pay no query and change no behaviour.

Chosen over an `Exists()` annotation because `filter_catalog_courses` already
stacks `.distinct()` and `.annotate(Count(...))`; an annotation would be
evaluated across the full matched set before the `LIMIT`, and would couple a
pure, independently-tested queryset builder to request auth state.

**Additive side effect:** `EnrollmentSerializer` nests
`CatalogCourseListSerializer`, so `/my-courses/` responses now carry
`course.is_wishlisted: false`.

**N+1 warning:** `get_learner_wishlist` must keep
`prefetch_related('course__instructors')` and
`select_related('course__category', 'course__created_by')`. Dropping them costs
20 extra queries per page.

---

## 6. Learner notes

`LearnerNote` is a private, learner-owned note optionally anchored to a course,
a lecture, and a playback timestamp.

| Decision | Choice | Why |
|---|---|---|
| `tags` type | `JSONField(default=list)` + `GinIndex` | Every list field in this codebase is JSON (`stream_renditions`, `rubric`, `criterion_results`). On Postgres `tags__contains=['react']` compiles to jsonb `@>` and is served by the GIN index — identical ergonomics to `ArrayField` without introducing the only Postgres-only *field type* in the project. `jsonb_ops` needs no extension (unlike the `pg_trgm` indexes elsewhere). |
| `color` | `TextChoices` enum, six swatches | A free-form hex string echoed into the DOM is a style-injection surface, and it makes the palette a frontend-only contract that cannot be themed server-side. |
| `course` / `lecture` `on_delete` | `SET_NULL` | Notes are the learner's own work product; a course teardown must not destroy them. Documented trade-off: an orphaned note keeps its content but loses its anchor and drops out of `?course=` filters. |
| Enrollment required to file a note? | **No** — published course is enough | A note stores zero course content, only the learner's own text. Gating on enrollment protects nothing while breaking note-taking before enrolling and after unenrolling. |
| Not-found on `notes/<pk>/` | **404, never 403** | Project policy for non-enumerable numeric IDs. Applies to GET, PATCH and DELETE alike. |
| `?tag=a&tag=b` | **AND** | The useful semantic for progressive filtering. |

`ordering = ['-is_pinned', '-updated_at', '-id']` with a matching
`idx_note_user_pin_upd`, so the default list is an index scan. Pinned notes sort
first regardless of `?ordering=`. Two `CheckConstraint`s make the invariants
durable: a timestamp requires a lecture, and the body cannot be empty.

`_validate_note_params` collects **every** field error before raising, matching
`_validate_catalog_params` — two bad params yield one 400 carrying both keys.

---

## 7. Query budget

| Endpoint | Queries | Scales with data? | Main N+1 risk |
|---|---|---|---|
| `learner/dashboard/summary/` | 4 | No | — (aggregates only) |
| `learner/activity/` | 6 | No (capped at 200/source) | dropping `select_related` on any source → one query per item |
| `learner/upcoming/` | 4 | No (capped by `limit`) | missing `.distinct()` duplicates section rows |
| `learner/continue/` | ~8 | No | inherited from `load_learner_curriculum` (already batched) |
| `my-certificates/` | 2 | No | missing `select_related('enrollment__course')` |
| `wishlist/` | 4 | No | **missing `prefetch_related('course__instructors')` → 10 queries/page** |
| `notes/` | 2 | No | missing `select_related('lecture__section__course')` |
| `catalog/` (modified) | +1 | No | computing the id set *before* pagination would scan the full filtered set |

---

## 8. Testing note

Tests live in `courses/all_tests/test_learner_dashboard.py`, `test_wishlist.py`,
`test_learner_notes.py`, `test_my_certificates.py`.

**Fixture gotcha:** `courses/signals.py` fires `recalculate_progress` on
`WatchProgress` post_save, which at 100% schedules certificate issuance via
`transaction.on_commit`. Those callbacks do **not** run under `APITestCase` —
wrap the completing write in `self.captureOnCommitCallbacks(execute=True)`, or
create the `Certificate` row directly (which is what the certificate tests do,
since they are about the list endpoint, not issuance).

---

## 9. Two completion bugs fixed alongside this work

Both predate the dashboard, but the new `courses_completed` tile is what made
them visible.

**`completed_at` was not sticky.** `recalculate_progress` used to clear it
whenever progress fell back below 100:

```python
elif progress < 100 and enrollment.completed_at is not None:
    enrollment.completed_at = None      # removed
```

`total_items` counts every `SectionContent` row in the course, so an
instructor adding one lecture silently un-completed everyone who had already
finished — on their next watch tick or submission the course dropped out of
the My Courses "Completed" tab and out of `courses_completed`. The certificate
is issued through `get_or_create` and is never revoked, so the two records
contradicted each other: a learner held a certificate for a course the
platform no longer called complete.

Completion is now permanent. The learner did finish the course as it existed
at the time, and that is what `completed_at` records; `progress_percent` still
moves, so later additions show up honestly as an unfinished remainder. Locked
by `test_adding_content_after_completion_does_not_uncomplete`.

**My Courses only ever loaded one page.** The frontend called
`/my-courses/` with no `page_size`, took `data.results`, and then filtered and
paginated *client-side* — so enrollment 11 onward was unreachable in every tab
and the tab counts were wrong. The list orders by `last_accessed_at DESC NULLS
LAST`, so a finished course the learner had stopped opening was the first
thing to fall off the end.

The endpoint now takes `?status=all|in_progress|completed` (validated;
unknown → 400) and returns a `status_counts` block beside the paginator keys:

```json
{"success": true, "data": {
  "count": 14, "next": "…", "previous": null, "results": [],
  "status_counts": {"all": 14, "in_progress": 13, "completed": 1}
}}
```

The counts have to be server-computed — they describe the whole enrollment
set, and a client counting rows in one page is wrong by construction.

**A completed course the learner later unenrolled from stays listed.**
`unenroll_learner` is a soft revoke: it flips `is_active` but preserves
progress and `completed_at`, and never revokes the certificate. Filtering the
list on `is_active=True` therefore hid a course the learner had genuinely
finished — the certificate had no course to open from, My Courses reported
**0 completed**, and the dashboard summary (which has never filtered on
`is_active`) reported **1**. That contradiction is what surfaced the bug.

`_learner_enrollment_scope` widens the base filter to
`Q(is_active=True) | Q(completed_at__isnull=False)` behind an **opt-in**
`include_unenrolled_completed` flag. Opt-in, not default, because
`get_continue_target` shares the same queryset and must never resume a course
the learner no longer has access to — it keeps the default active-only scope,
covered by `test_unenrolled_course_is_never_the_resume_target`.

Only unenrolled *and unfinished* rows stay hidden. The `in_progress` filter
and count both pin `is_active=True`, so an unenrolled completed course appears
under **Completed** and nowhere else. The frontend routes those cards to the
certificate rather than the player, and labels them *Completed · access
ended*.

Callers that need every enrollment rather than a page — the catalog's enrolled
flags, the Q&A course selector, the note editor's course picker — now pass
`page_size: ALL_ENROLLMENTS_PAGE_SIZE`. Any new caller of `useMyCourses()`
must do the same or it will silently see only the first ten.

---

## 10. Not built

Deferred, and deliberately not faked anywhere in these endpoints: XP / badges /
streak ledger and leaderboard, learning paths, study groups, consultancy
booking, course recommendations, AI assistant and player copilot, live-room
WebRTC signalling, and learner-facing active-session management and 2FA
(`admin_console` sessions are admin-only). See `FEATURE_STATUS.md`.
