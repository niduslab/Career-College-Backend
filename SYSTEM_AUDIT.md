# System Audit — Catalog Filtering & Sorting

**Date opened:** 2026-05-21
**Last updated:** 2026-05-21
**Scope:** the multi-criteria catalog filter/sort wiring in
`courses/services/enrollment_service.py` → `filter_catalog_courses`, plus
the surfaces it touches (`CatalogCourseListView`, `CatalogCourseListSerializer`,
and the queryset returned by `get_catalog_courses`).

## TL;DR

Catalog list path has no N+1 — `select_related('created_by', 'category')` +
`prefetch_related('instructors')` covers everything the list serializer reads.
The original sweep surfaced one critical, two high-severity, six medium,
and three low-severity items. **Every actionable code issue is now closed**
except M3 (blocked on a model field). L1 stays open as a documented future
hotspot — it's not broken today, and the prescribed fix is a schema-level
denormalization that should wait until EXPLAIN on real data proves the need.

## Closed issues

These were fixed in the 2026-05-21 pass. Code references point at the
current implementation; if you need the original problem narrative, see
git history for the prior version of this file.

| ID | Severity | What was wrong | What shipped |
|---|---|---|---|
| C1 | Critical | Instructor name search hit `first_name` / `last_name` (unpopulated AbstractUser columns), silently returning zero matches. | Search now ORs against `instructors__full_name`. See [`enrollment_service.py:230-239`](courses/services/enrollment_service.py#L230-L239). |
| H1 | High | `?category=X&subcategory=Y` ANDed two equality predicates on the same single FK column → always empty. | Three-way conditional joins through `category__parent`. See [`enrollment_service.py:170-191`](courses/services/enrollment_service.py#L170-L191). |
| H2 | High | `?category=parent` didn't include rows tagged with a subcategory of that parent. | Same branch as H1 — category-only path broadens to `Q(category__slug) | Q(category__parent__slug)`. |
| M1 | Medium | `description__icontains` had no supporting index — seq-scan on large catalogs. | GIN trigram indexes on `title` and `description`; `pg_trgm` enabled. See [`0007_add_catalog_filter_indexes.py`](courses/migrations/0007_add_catalog_filter_indexes.py). **Deploy note:** the migrating Postgres role needs `CREATE EXTENSION` privilege. |
| M2 | Medium | `price` and `duration_minutes` range filters had no index. | Composite btrees `(is_published, price)` and `(is_published, duration_minutes)`, same migration. |
| M4 | Medium | Unknown `?sort=foo` silently fell back to default. | `_validate_catalog_params` raises `ValidationError`; view returns 400 listing valid sort keys. See [`enrollment_service.py:77-130`](courses/services/enrollment_service.py#L77-L130) and [`enrollment_views.py:47-54`](courses/all_views/enrollment_views.py#L47-L54). |
| M5 | Medium | Negative or non-numeric `price_min`/`price_max`/`duration_min`/`duration_max` were silently accepted. | Same validator path — 400 on non-numeric or negative values. |
| M6 | Medium | `?level=foobar` silently returned zero rows. | Same validator path — 400 listing the bad tokens and the valid set. |
| L2 | Low | `_csv_param` docstring claimed it deduped; it didn't. | Now dedupes via `dict.fromkeys`, order preserved. See [`enrollment_service.py:50-60`](courses/services/enrollment_service.py#L50-L60). |

---

## Open issues

### M3. `?sort=rating` is a silent no-op

**Status: OPEN** — blocked on a model field.

The `'rating'` branch of `_apply_catalog_sort`
([`enrollment_service.py:289-292`](courses/services/enrollment_service.py#L289-L292))
falls back to `published_at` ordering because there is no `avg_rating`
column on `NidusCourse` (or equivalent aggregate). Frontend has no
signal that the requested sort was ignored — UI may show "Sort by:
Top-rated" while the rows are actually newest-first.

**Why it matters:** a learner clicks "Sort by: Top rated" and trusts
the dropdown label. The grid below is still ordered by publish date,
so a 1.2-star course can sit above a 4.9-star course. Once the rating
field actually ships, this gets *worse*: frontend code that worked
around the bug ("just call it newest, I guess") keeps showing newest
while the API silently starts honoring `rating`, and the sort behavior
appears to "drift" with no deploy explaining it.

**Recommended interim:** drop `'rating'` from `CATALOG_SORT_OPTIONS`.
With M4 fixed, `?sort=rating` would then return an honest 400 listing
the currently valid sorts. Re-add `'rating'` to the set when
`NidusCourse.avg_rating` lands and the corresponding branch in
`_apply_catalog_sort` is wired up.

### L1. Popularity-sort + search path is the heaviest in the filter set

**Status: OPEN (informational)** — no fix today; revisit with EXPLAIN
once the catalog grows.

When both `?search=` and `?sort=popularity` are supplied, the generated
SQL stacks:

- `LEFT JOIN nidus_courses_instructors` + `LEFT JOIN users` (for search)
- `LEFT JOIN enrollments` (for `Count('enrollments', distinct=True)`)
- `SELECT DISTINCT` (from the search's `.distinct()`)
- `GROUP BY` on the SELECT'd columns

It is correct — `Count(distinct=True)` defends the count against the
instructor-join fan-out — but it's the heaviest path in the filter set.
With a debounced search input (300 ms) on a busy catalog page that
defaults to popularity sort, that's 3–4 of these joined queries per
second per active user.

**Action when triggered:** EXPLAIN on representative data (~10k+
published rows) and, if the plan stops being index-driven, materialize
`enrollment_count` onto `NidusCourse` via the `Enrollment` post_save
signal so the popularity sort can `ORDER BY` a denormalized column
instead of an aggregate.

## N+1 sweep — clean

Walked the request path end-to-end for `GET /api/v1/courses/catalog/`:

| Touchpoint | What it reads | How it's loaded | Verdict |
|---|---|---|---|
| `CatalogCourseListView.get` | queryset | `get_catalog_courses()` base | — |
| `CatalogCourseListSerializer.thumbnail` | `ImageField` | model attribute | OK |
| `CatalogCourseListSerializer.category` (FK) | `category.id/name/slug` | `select_related('category')` | OK |
| `CatalogCourseListSerializer.instructors` (M2M) | `id/full_name/email` per instructor | `prefetch_related('instructors')` | OK |
| `InstructorBriefSerializer.full_name` | direct column | already in user table | OK |
| `StandardResultsSetPagination` | `count(*)` | separate query | expected |

Catalog list endpoint emits **3 queries total** per page (main +
instructor prefetch + count). The prefetch is one extra IN-query per
request, not an N+1 — do not "optimize" it into a JOIN, that would
explode row count and confuse pagination.

---

## Out-of-scope follow-ups noticed during the fix pass

- `price_type` is not validated — `?price_type=garbage` silently
  becomes a no-op. Same shape as the M6 bug; add to
  `_validate_catalog_params` if you want symmetry.
- Once production traffic exists, watch p95 of `/catalog/?search=…`
  to confirm the new trigram indexes are getting picked up by the
  planner.
