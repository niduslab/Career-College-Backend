# Performance Audit: `MyCoursesDetailView` — `/my-courses/<slug>/`

> **Date:** 2026-05-17  
> **Scope:** View, service layer, serializers, and DB query pattern for the enrolled course detail endpoint.

> **Status (2026-05-17, later same day):** Issues #1, #3, #4, #5, #6 are RESOLVED. Issue #2 (caching) is still open but lower priority now that the response is small.
>
> - **#1, #4, #6** — Split-endpoint refactor: `MyCoursesDetailView` was slimmed to return only course metadata + enrollment status (now serialized by `MyCourseDetailSerializer`). The curriculum tree moved to the new `LearnerCurriculumView` (`GET /learn/<slug>/curriculum/`) which loads lightweight fields with `.only(...)`, and per-item content moved to `LearnerLectureDetailView` (`GET /learn/lectures/<id>/`). `load_consumption_curriculum` and the `_Consumption*` serializer family were deleted.
> - **#3** — `MyCoursesDetailView` now does the instructor check in Python against the prefetched `course.instructors.all()` list (zero extra queries). `resolve_course_access` in `learner_service.py` uses the same pattern; the new learner views/loader prefetch `instructors` (or `section__course__instructors`) so the check is also free there.
> - **#5** — `update_last_accessed` is now debounced by 5 minutes via `LAST_ACCESSED_DEBOUNCE`. Repeated GETs / progress writes within that window skip the row UPDATE.
>
> The audit below describes the **pre-refactor** state and is kept for reference.

---

## Endpoint Overview

`MyCoursesDetailView` is the enrolled learner's (and course instructor's) primary course consumption endpoint. When hit, it returns the **entire course tree** in a single response — metadata, all sections, every lecture (with HLS URLs and full article HTML), every quiz (with questions and answers), every coding exercise (with configs and test cases), every assignment (with questions), and the learner's per-lecture watch progress.

### Request Flow

```
GET /my-courses/<slug>/
        │
        ▼
┌─────────────────────────┐
│  1. Fetch course with   │  ── 6 queries (course + 5 prefetches)
│     select_related &    │
│     prefetch_related    │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  2. Check if user is    │  ── 1 REDUNDANT query (bypasses prefetch)
│     an instructor       │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  3. Check enrollment    │  ── 1 query (learner path only)
│     + update last_      │  ── 1 write query (every single GET)
│     accessed            │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  4. load_consumption_   │  ── 12-14 queries (sections, lectures,
│     curriculum()        │     quizzes, coding, assignments,
│                         │     watch progress, video durations)
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  5. Serialize full tree │  ── 0 extra queries (reads from context)
│     via EnrolledCourse  │
│     ContentSerializer   │
└─────────────────────────┘

Total: ~20-22 DB queries per request
```

---

## Issue #1: Entire Course Tree Returned in One Response

### The Problem

The endpoint returns **everything** — every section, every lecture with full `article_content` (which can be thousands of characters of HTML), every quiz with all questions and answers, every coding exercise with all language configs and test cases, every assignment with all questions.

For a large course (15 sections, 100+ content items), the JSON payload can easily reach **several megabytes**. The learner only needs the item they're currently viewing, but the server does all the work to fetch and serialize the entire tree every time.

### Where It Happens

**File:** `courses/services/curriculum_service.py` — `load_consumption_curriculum()`

This function bulk-loads every content type for the entire course in one call.

**File:** `courses/all_serializers/enrollment_serializers.py` — `EnrolledCourseContentSerializer`

The serializer chain nests everything into a single deeply nested JSON tree.

### The Impact

- **Slow response times** — more data to fetch, serialize, and transmit
- **High memory usage** — the full tree is materialized in server memory before sending
- **Wasted bandwidth** — the client receives content it may never view
- **Scales poorly** — response time grows linearly with course size

### The Solution: Lazy Loading (Per-Section / Per-Item Fetching)

Split the single monolithic response into smaller, on-demand requests:

1. **Curriculum outline endpoint** — returns just section titles, item titles, item types, and positions (lightweight)
2. **Per-item detail endpoints** — fetch a specific lecture, quiz, or coding exercise only when the learner clicks on it

**Good news:** The codebase already has this pattern started in the `/learn/` endpoints:

| Endpoint | What it does |
|---|---|
| `GET /learn/<slug>/curriculum/` | Returns the light outline only |
| `GET /learn/lectures/<id>/` | Returns one lecture with HLS URLs or article content |
| `POST /learn/lectures/<id>/progress/` | Updates watch progress for one lecture |

**Recommendation:** Build out the `/learn/` surface to cover quizzes, coding exercises, and assignments. Once complete, deprecate `/my-courses/<slug>/` for active consumption. The frontend loads the outline first, then fetches individual items as the learner navigates — dramatically reducing initial load time.

---

## Issue #2: No Response Caching

### The Problem

Every request to this endpoint runs all 20+ database queries and re-serializes the full tree from scratch — even if the course content hasn't changed since the last request. Published courses change rarely (maybe an instructor fixes a typo once a month), but learners may visit the same course page dozens of times a day.

### Where It Happens

**File:** `courses/all_views/enrollment_views.py` — `MyCoursesDetailView.get()`

No caching headers (ETag, Cache-Control, Last-Modified) are set. No application-level cache (Redis) is consulted before running the full query chain.

### The Impact

- **Unnecessary DB load** — identical queries repeated on every visit
- **Wasted CPU** — serialization of unchanged data on every request
- **Higher latency** — every request pays the full cost even for repeat visits

### The Solution: Two-Layer Caching

**Layer 1 — Application cache (Redis):**

Cache the serialized course content (the part that's the same for all users) in Redis, keyed by course slug and a content version hash. Invalidate when the instructor edits the course.

```python
# Pseudocode
cache_key = f"consumption_curriculum:{course.slug}:v{course.content_version}"
cached = cache.get(cache_key)
if cached is None:
    cached = load_consumption_curriculum(course, ...)
    cache.set(cache_key, cached, timeout=3600)
```

Personal data (watch progress, enrollment info) is still fetched live and merged in.

**Layer 2 — HTTP caching headers:**

For the static course content portion, set `Cache-Control` and `ETag` headers so the client (or a CDN) can serve cached responses without hitting the server at all.

**Invalidation:** Bump the course's `content_version` (or `updated_at`) whenever an instructor edits any content. The old cache key becomes stale automatically.

---

## Issue #3: Redundant Instructor Check (1 Wasted Query)

### The Problem

The view prefetches the course's `instructors` M2M relationship in the initial query, then immediately calls `.filter().exists()` on that same relationship — which **bypasses the prefetch cache** and hits the database again.

### Where It Happens

**File:** `courses/all_views/enrollment_views.py` — `MyCoursesDetailView.get()`, around line 232

```python
# Current code — hits DB despite prefetch
is_instructor = course.instructors.filter(pk=request.user.pk).exists()
```

Django's `.filter()` on a prefetched queryset always generates a new SQL query. The prefetch cache is only used when you call `.all()` on the relation.

### The Solution

Use Python-side filtering against the already-loaded data:

```python
# Fixed — uses the prefetched data, zero extra queries
is_instructor = request.user.pk in {u.pk for u in course.instructors.all()}
```

This is a one-line fix with zero risk.

---

## Issue #4: Lectures Loaded Without `.only()` (Oversized Rows)

### The Problem

The consumption service fetches all lecture fields from the database, including `article_content` — a `TextField` that can hold entire articles of HTML. For video-type lectures (which are the majority), this field is empty or irrelevant, but the database still reads and transfers it.

### Where It Happens

**File:** `courses/services/curriculum_service.py` — inside `load_consumption_curriculum()`

```python
# Current code — fetches every column
lectures = Lecture.objects.filter(id__in=lecture_ids)
```

Compare with the catalog version in the same file, which already restricts fields:

```python
# Catalog version — only fetches what's needed
lectures = Lecture.objects.filter(id__in=lecture_ids).only(
    'id', 'title', 'lecture_type', 'is_preview', ...
)
```

### The Impact

For a course with 50 lectures where 45 are video type, you're transferring 45 empty `article_content` fields (plus any other unused large fields) from the database for no reason. PostgreSQL still has to read those TOAST pages.

### The Solution

Add `.only()` to restrict to the fields the serializer actually uses:

```python
lectures = Lecture.objects.filter(id__in=lecture_ids).only(
    'id', 'title', 'lecture_type', 'is_preview',
    'article_content',           # needed for article lectures
    'stream_master_playlist',    # needed for video lectures
    'created_at', 'updated_at',
)
```

Alternatively, if the endpoint is refactored to lazy-load per item (Issue #1), this becomes less important since you'd only fetch one lecture at a time.

---

## Issue #5: Synchronous `update_last_accessed` on Every GET

### The Problem

Every learner GET request triggers a synchronous database `UPDATE` to stamp the current time on `enrollment.last_accessed_at`. This happens even if the learner refreshed the page 2 seconds ago. The write adds latency to what should be a read-only request.

### Where It Happens

**File:** `courses/all_views/enrollment_views.py` — `MyCoursesDetailView.get()`

```python
update_last_accessed(enrollment)  # runs on every single GET
```

### The Impact

- **Added write latency** on every read request
- **Row-level lock contention** under concurrent access
- **Pointless precision** — nobody needs last-accessed timestamps accurate to the second

### The Solution

**Option A — Debounce (simplest):**

Only update if the current `last_accessed_at` is older than a threshold (e.g., 5 minutes):

```python
from django.utils import timezone
from datetime import timedelta

def update_last_accessed(enrollment):
    threshold = timezone.now() - timedelta(minutes=5)
    if enrollment.last_accessed_at is None or enrollment.last_accessed_at < threshold:
        Enrollment.objects.filter(pk=enrollment.pk).update(
            last_accessed_at=timezone.now()
        )
```

**Option B — Defer to Celery:**

Hand the write off to a background task so the learner doesn't wait for it:

```python
update_last_accessed_task.delay(enrollment.pk)
```

Celery is already set up in this project for the video transcoding pipeline, so no new infrastructure is needed.

---

## Issue #6: No Pagination at Any Level

### The Problem

The sections list and the contents within each section are returned in full, with no pagination. A course with 20 sections, each containing 10 items, returns all 200 items in one response.

### Where It Happens

**File:** `courses/services/curriculum_service.py` — the entire tree is assembled without any slicing.

**File:** `courses/all_serializers/enrollment_serializers.py` — serializers iterate the full context dicts.

### The Impact

Response size and serialization time grow unbounded as courses get larger.

### The Solution

This is best addressed by Issue #1's lazy loading approach rather than traditional offset-based pagination. Course curricula are tree-shaped (sections → items), not flat lists, so standard DRF pagination doesn't fit naturally. Per-section or per-item fetching is the right model here.

---

## Summary Table

| # | Issue | Impact | Effort | Priority |
|---|---|---|---|---|
| 1 | Entire tree in one response | High — payload size, latency, memory | Medium — `/learn/` endpoints already started | **P0** |
| 2 | No caching | High — redundant work on every visit | Medium — Redis already available via Celery | **P0** |
| 3 | Redundant instructor query | Low — 1 extra query | Trivial — one-line fix | **P2** |
| 4 | Lectures without `.only()` | Medium — oversized DB reads | Low — add `.only()` clause | **P1** |
| 5 | Synchronous `update_last_accessed` | Low-Medium — write on every read | Low — debounce or Celery task | **P1** |
| 6 | No pagination | Medium — unbounded response growth | Addressed by #1 | **P0** (via #1) |

### Recommended Execution Order

1. **Quick wins first** (can ship in an afternoon): Fix #3 (instructor check) and #5 (debounce last_accessed)
2. **Medium effort** (1-2 days): Fix #4 (`.only()` on lectures) and #2 (Redis caching layer)
3. **Architectural** (ongoing): Complete #1 (build out `/learn/` endpoints for quizzes, coding, assignments → deprecate the monolithic endpoint)
