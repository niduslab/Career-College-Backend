# 15 — Course Review & Rating System

## Overview

Enrolled learners can leave a review (1–5 star rating, headline, optional body) for any course they are actively enrolled in. Reviews are published immediately and the learner can edit or delete their own review at any time. Other learners can cast a helpful / not-helpful vote on any review they did not write. Aggregate statistics (`avg_rating`, `review_count`) are denormalized onto `NidusCourse` for O(1) catalog sort and filter — no subquery needed on each catalog page load.

---

## Data Model

### `CourseReview` (table: `course_reviews`)

```
course_reviews
├── id                  BIGINT PK (auto)
├── enrollment_id       FK → enrollments (OneToOne, CASCADE)  ← primary uniqueness key
├── user_id             FK → users (CASCADE)
├── course_id           FK → nidus_courses (CASCADE)
├── rating              SMALLINT (1–5, DB CHECK constraint)
├── headline            VARCHAR(150)
├── body                TEXT (blank allowed, default '')
├── is_published        BOOLEAN (default True, db_index)
├── helpful_count       INT (default 0, denormalized)
├── not_helpful_count   INT (default 0, denormalized)
├── created_at          TIMESTAMPTZ (auto)
└── updated_at          TIMESTAMPTZ (auto)
```

**Uniqueness (two layers):**

| Layer | Mechanism | Why |
|---|---|---|
| Primary | `OneToOneField(enrollment)` → schema-level UNIQUE | One review per enrollment |
| Belt-and-braces | `UniqueConstraint(user, course)` | Guards against concurrent creates that slip past the application layer |

A DB-level `CheckConstraint(rating 1–5)` prevents out-of-range values even if validation is bypassed.

**Indexes:**

| Index | Covers |
|---|---|
| `(course, -created_at)` | Default list sort — newest first |
| `(course, -helpful_count)` | Sort by most helpful |
| `(course, rating)` | Star filter + rating sort |
| `(is_published, course)` | Fast published-only queries |

### `ReviewVote` (table: `review_votes`)

```
review_votes
├── id            BIGINT PK (auto)
├── review_id     FK → course_reviews (CASCADE)
├── voter_id      FK → users (CASCADE)
├── is_helpful    BOOLEAN
└── created_at    TIMESTAMPTZ (auto)
```

`UniqueConstraint(review, voter)` — one vote row per `(review, voter)` pair. The row is **mutated** (flag flipped) rather than deleted and re-created when a voter changes direction. This keeps the history compact and the counter update atomic.

### `NidusCourse` additions

```
nidus_courses
├── avg_rating    DECIMAL(3,2)  default 0.00   ← denormalized
└── review_count  INT           default 0      ← denormalized
```

New catalog-sort indexes:

```
idx_ncourse_pub_rating   (is_published, avg_rating)
idx_ncourse_pub_reviews  (is_published, review_count)
```

---

## Why Denormalize `avg_rating` / `review_count`?

Computing `AVG(rating)` live on every catalog page load would require a subquery or JOIN against `course_reviews` for every row in the paginated result set. The catalog is the highest-traffic endpoint — guests, unenrolled learners, and search engines all hit it without authentication.

By denormalizing, the catalog remains a single-table scan. The trade-off is a brief lag (typically < 1 s) between a review write and the updated stats appearing on the catalog. The `_recalculate_course_avg` function runs on the same Django worker process via `transaction.on_commit` — no Celery task, no network hop.

---

## Service Layer: `courses/services/review_service.py`

### `ReviewError`

```python
class ReviewError(Exception):
    def __init__(self, message: str, http_status: int = 422):
        self.message = message
        self.http_status = http_status
```

Mirrors `AssignmentSubmissionError` and `InviteError`. Views catch `ReviewError` and use `exc.http_status` directly — no lookup table needed.

### Public API

| Function | Returns | Raises |
|---|---|---|
| `create_or_update_review(user, course, data)` | `(review, created: bool)` | `ReviewError(403)` — not actively enrolled |
| `delete_review(user, course)` | `None` | `ReviewError(404)` — no review exists |
| `vote_on_review(voter, review_id, is_helpful)` | `ReviewVote` | `ReviewError(404)` — review missing/unpublished; `ReviewError(422)` — self-vote |
| `get_course_reviews(course, params, requesting_user)` | `QuerySet[CourseReview]` | — |
| `get_review_summary(course)` | `dict` | — |
| `get_my_review(user, course)` | `CourseReview` | `CourseReview.DoesNotExist` |

### `create_or_update_review` — upsert via enrollment key

```python
review, created = CourseReview.objects.update_or_create(
    enrollment=enrollment,
    defaults={'user': user, 'course': course, **validated_data},
)
transaction.on_commit(lambda: _recalculate_course_avg(course.pk))
```

The `enrollment` field is the lookup key. On an edit, the existing row is updated in-place. The on-commit callback fires after the outer `atomic()` block commits — or immediately if called outside any transaction.

### Vote flip atomicity (`vote_on_review`)

`select_for_update()` on the existing `ReviewVote` row serializes concurrent votes on the same review. When a voter flips direction:

1. Both counter fields updated in a single `UPDATE` with `F()` expressions — no read-modify-write race:
   ```python
   CourseReview.objects.filter(pk=review.pk).update(
       **{old_field: F(old_field) - 1, new_field: F(new_field) + 1, 'updated_at': now}
   )
   ```
2. `existing.is_helpful` flipped and saved.

Same-direction click is a no-op — the existing vote is returned unchanged. Self-vote check runs before the transaction opens, so no lock is acquired for the trivially-rejected case.

### `_recalculate_course_avg` (internal)

Single aggregate + targeted update:

```python
agg = CourseReview.objects.filter(course_id=course_id, is_published=True).aggregate(
    avg=Avg('rating'), count=Count('id')
)
NidusCourse.objects.filter(pk=course_id).update(
    avg_rating=round(float(agg['avg'] or 0), 2),
    review_count=agg['count'],
    updated_at=timezone.now(),
)
```

No full model load, no other field touched. Called via `transaction.on_commit` after every review write or delete — never fires on a rolled-back transaction.

---

## API Endpoints

All under `/api/v1/courses/`. Route registration in `courses/urls.py` — literal path segments (`summary/`, `my-review/`) are declared before the plain list path to avoid Django URL resolver ambiguity.

### Viewer-vote annotation

`CourseReviewListView.get` annotates the queryset with one `Subquery` before pagination when the caller is authenticated:

```python
viewer_vote_sq = ReviewVote.objects.filter(
    review=OuterRef('pk'), voter=request.user
).values('is_helpful')[:1]
reviews = reviews.annotate(_viewer_vote=Subquery(viewer_vote_sq))
```

One extra DB query per page-load, not per review row. Unauthenticated requests skip the annotation; the serializer's `get_viewer_vote` falls back to `None`.

### Endpoint table

| Method | URL | Auth | Description |
|---|---|---|---|
| GET | `<slug>/reviews/` | AllowAny | Paginated published reviews. `?rating=1-5`, `?ordering=(-created_at\|created_at\|-helpful_count\|-rating\|rating)`. Annotates `_viewer_vote` for auth callers. |
| POST | `<slug>/reviews/` | IsLearnerUser | Upsert own review. 201 on create, 200 on update. |
| GET | `<slug>/reviews/summary/` | AllowAny | Aggregate stats: `avg_rating`, `review_count`, `distribution` (1–5 star counts). |
| GET | `<slug>/reviews/my-review/` | IsLearnerUser | Fetch own review. 404 if none exists. |
| PATCH | `<slug>/reviews/my-review/` | IsLearnerUser | Update own review. |
| DELETE | `<slug>/reviews/my-review/` | IsLearnerUser | Delete own review. |
| POST | `reviews/<int:review_id>/vote/` | IsLearnerUser | Cast or flip helpful / not-helpful vote. |

### Access-denied policy

| Identifier | Response on no-access | Reason |
|---|---|---|
| Slug (`<slug>/reviews/*`) | 404 when course not found/not published | Slug is already in the catalog — but a missing/unpublished course has no listing to protect |
| Numeric ID (`reviews/<review_id>/vote/`) | 404 when review missing or unpublished | IDs are not publicly enumerable; 403 would confirm existence |
| Self-vote | 422 | Business-rule violation, not an access-control error |

---

## Catalog Integration

The review system wires into the existing catalog filter/sort pipeline in `courses/services/enrollment_service.py`:

| Param | Behavior |
|---|---|
| `?sort=rating` | `ORDER BY avg_rating DESC NULLS LAST, published_at DESC, -id` |
| `?rating_min=<1.0–5.0>` | `filter(avg_rating__gte=rating_min)` |
| `?min_reviews=<N>` | `filter(review_count__gte=N)` |

Validation errors for these params follow the same pattern as other catalog params — 400 with a field-keyed `errors` dict.

---

## Serializers (`courses/all_serializers/review_serializers.py`)

| Class | Direction | Notes |
|---|---|---|
| `CourseReviewReadSerializer` | Output | Includes `reviewer_name` (from `user.full_name`), `viewer_vote` (from `_viewer_vote` annotation: `'helpful'`, `'not_helpful'`, or `None`). |
| `CourseReviewWriteSerializer` | Input | `rating` (int 1–5), `headline` (str ≤150, non-blank after strip), `body` (optional). |
| `CourseReviewSummarySerializer` | Output | `avg_rating` (float), `review_count` (int), `distribution` (dict `'1'`–`'5'` → count). |
| `ReviewVoteSerializer` | Input | Single field: `is_helpful` (boolean). |

---

## Admin (`courses/admin.py`)

`CourseReview` is registered with:
- `is_published` toggle (moderators can unpublish individual reviews)
- Bulk actions: `publish_reviews`, `unpublish_reviews`
- `raw_id_fields` on `user`, `course`, `enrollment`
- `readonly_fields` on `helpful_count`, `not_helpful_count` (counters are maintained by the service, not set by admin)

`ReviewVote` is registered for lookup and audit. No bulk actions needed — votes are read-only from the admin surface.

---

## Sequence: Create / Edit Review

```
Learner POSTs <slug>/reviews/
        │
        ├── _get_published_course_or_404(slug) — 404 if not found
        ├── CourseReviewWriteSerializer.is_valid() — 400 on bad input
        └── create_or_update_review(user, course, data)
                │
                ├── Check active enrollment — ReviewError(403) if none
                ├── atomic: CourseReview.update_or_create(enrollment=enrollment, ...)
                └── transaction.on_commit → _recalculate_course_avg(course.pk)
                            │
                            └── Single aggregate query + NidusCourse.UPDATE
                                (avg_rating, review_count, updated_at)
```

---

## Sequence: Vote Flip

```
Learner POSTs reviews/<id>/vote/  { "is_helpful": false }
        │
        ├── ReviewVoteSerializer.is_valid()
        └── vote_on_review(voter, review_id, is_helpful=False)
                │
                ├── CourseReview.get(pk=review_id, is_published=True) — 404 if missing
                ├── Self-vote check — ReviewError(422) if voter == review.user
                └── atomic:
                        ├── ReviewVote.select_for_update().filter(review, voter)
                        ├── If exists + same direction → no-op, return existing
                        ├── If exists + different direction →
                        │       UPDATE course_reviews SET helpful_count=F-1, not_helpful_count=F+1
                        │       UPDATE review_votes SET is_helpful=False
                        └── If new →
                                INSERT review_votes
                                UPDATE course_reviews SET not_helpful_count=F+1
```

---

## Files

| File | Role |
|---|---|
| `courses/all_models/review_models.py` | `CourseReview` and `ReviewVote` models |
| `courses/all_models/course_models.py` | `avg_rating`, `review_count` fields + catalog indexes on `NidusCourse` |
| `courses/services/review_service.py` | `ReviewError`, all service functions, `_recalculate_course_avg` |
| `courses/all_serializers/review_serializers.py` | `CourseReviewReadSerializer`, `CourseReviewWriteSerializer`, `CourseReviewSummarySerializer`, `ReviewVoteSerializer` |
| `courses/all_views/review_views.py` | `CourseReviewListView`, `CourseReviewSummaryView`, `MyReviewView`, `ReviewVoteView` |
| `courses/admin.py` | `CourseReviewAdmin`, `ReviewVoteAdmin` |
| `courses/migrations/0013_coursereview_reviewvote_niduscourse_avg_rating_and_more.py` | DB migration |

---

## Future Phases

| Feature | Notes |
|---|---|
| Instructor response | Add `instructor_response` + `instructor_responded_at` fields to `CourseReview`. Show inline on the list/detail serializer. |
| Review moderation queue | Add `is_flagged` field; flagged reviews enter a queue for admin review before re-publication. |
| AI sentiment analysis | Async Celery task on review create: run a lightweight classifier and store `sentiment_label` (`positive`/`neutral`/`negative`) on `CourseReview`. Surfaced as an admin filter, not a learner-facing field. |
| Verified-purchase badge | `CourseReview.enrollment` already proves enrollment. Front-end can display a "Verified Learner" badge if `enrollment.progress_percent > 0`. No backend change needed. |
