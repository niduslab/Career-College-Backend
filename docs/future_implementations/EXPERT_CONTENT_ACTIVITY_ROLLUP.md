# Expert Content Activity Rollup (Institution Monitoring)

**Status:** Planned — not yet implemented.
**Depends on:** `AuthoredModel` (`created_by` / `last_edited_by` on content models) — **already shipped** (migration `courses/0016`).
**SRS:** §7.2.1 "monitor expert course creation activity", "track expert-created content performance"; §7.7.3 audit/compliance.

## Problem

Authorship is currently surfaced **per row** — `created_by` / `last_edited_by` appear on every section, lecture, quiz, assignment, and coding-exercise read serializer. To answer "how much has each expert produced?", an institution admin would have to crawl every course → section → content endpoint and tally by hand. There is no aggregate view.

This feature adds **one institution-scoped endpoint** that rolls up content counts per expert across all the institution's courses.

## Endpoint

```
GET /api/v1/auth/partner/experts/activity/
```

**Auth:** `IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution` (identical to the other `partner/experts/...` endpoints). Always scoped to `request.user.partner_institution_profile` — an institution can only ever see its own experts.

### Query params (all optional)

| Param | Effect |
|---|---|
| `course_id` | Narrow the rollup to one course **owned by the institution**. Not owned / missing → `404` (numeric ID → 404, per project policy). |
| `expert_id` | Single expert (the expert's `User.id`). Not an affiliate of this institution → `404`. |
| `include_edits` | `true` → also report `last_edited_by` activity (edit counts + `last_edited_at`). Default `false` (creates only). |

### Response

```json
{
  "success": true,
  "data": {
    "experts": [
      {
        "expert": {"id": 12, "full_name": "Jane Roe", "email": "jane@example.com"},
        "sections": 6,
        "lectures": 24,
        "quizzes": 8,
        "assignments": 5,
        "coding_exercises": 3,
        "total_items": 46,
        "courses_contributed": 4,
        "last_authored_at": "2026-06-20T10:00:00Z"
      }
    ]
  }
}
```

- **Every active roster expert is listed**, including those who authored nothing (all counts `0`) — the dashboard shows the whole roster, not just contributors.
- `total_items` = sum of the per-type counts (excludes `sections` if you want "content items only" — decide at build time; the example includes everything except sections in the sum is a choice — document the final rule in code).
- `courses_contributed` = count of distinct courses the expert authored at least one item in.
- `last_authored_at` = most recent `created_at` across all the expert's authored content (null if none).
- When `include_edits=true`, add `edits` (count of rows where `last_edited_by = expert` and `last_edited_by != created_by` to avoid double-counting the create) and `last_edited_at`.

## Data model / query strategy

Every content model inherits `AuthoredModel` and has `created_by` (FK→User, `SET_NULL`). Institution scope is reached through the section→course chain:

| Model | Path to institution |
|---|---|
| `CourseSection` | `course__partner_institution` |
| `SectionContent` | `section__course__partner_institution` |
| `Lecture` | `section__course__partner_institution` |
| `Quiz` | `section__course__partner_institution` |
| `Assignment` | `section__course__partner_institution` |
| `CodingExercise` | `section__course__partner_institution` |

**Aggregate with one grouped query per content type** (NOT per expert — no N+1):

```python
Lecture.objects.filter(
    section__course__partner_institution=institution,
    created_by__isnull=False,
).values('created_by').annotate(
    n=Count('id'),
    last=Max('created_at'),
)
```

Then merge the per-type result rows in Python, keyed by `created_by` (the expert's user id). Total DB cost ≈ 6 grouped queries + 1 roster query, **independent of roster size**.

`courses_contributed`: per type, also pull `.values('created_by').annotate(courses=Count('section__course', distinct=True))` — but distinct-course-across-types can't be summed (an expert may touch the same course via a lecture AND a quiz). Two clean options:

1. **Exact:** one extra query over `SectionContent` (the union slot table) → `.values('created_by').annotate(c=Count('section__course', distinct=True))`. `SectionContent` covers lectures/quizzes/assignments/coding, so its distinct course count is authoritative for those four. Add sections separately if a section-only contribution should count.
2. **Approximate / simpler:** gather distinct `(created_by, course_id)` pairs from each type query and union the sets in Python.

Prefer option 1 — one query, exact for content items.

## Files to change

1. **`courses/services/institution_course_service.py`** — new
   `expert_content_activity(institution, *, course_id=None, expert_id=None, include_edits=False) -> list[dict]`.
   Owns all aggregation (content models live in the courses app). Returns rows already merged + zero-filled against the roster. Raise `InstitutionCourseError(http_status=404)` for a `course_id` / `expert_id` not owned by the institution.

2. **`authentication/all_views/partner_views.py`** — new `InstitutionExpertActivityView(APIView)`.
   - `permission_classes = [IsAuthenticated, IsEmailVerified, IsVerifiedPartnerInstitution]`
   - **Lazy-import** the service inside the method (`from courses.services.institution_course_service import expert_content_activity`) to avoid an `authentication → courses` circular import at module load.
   - Resolve `institution = request.user.partner_institution_profile`, parse/validate params, call service, wrap in the standard `{'success': True, 'data': {...}}` envelope.
   - `try/except InstitutionCourseError` → use `exc.http_status`; broad `except` → log + 500.

3. **`authentication/urls.py`** — register `partner/experts/activity/` → `InstitutionExpertActivityView`.
   Place it **before** the `partner/experts/<int:id>/` route. (`<int:id>` won't match the literal `activity` anyway, but ordering it first removes all doubt.)

4. **`authentication/serializers.py`** — optional `ExpertActivitySerializer`. Lean approach: return plain dicts from the service (all values are server-computed counts, nothing to validate). Reuse `InstructorBriefSerializer` shape for the nested `expert` block (or hand-build `{id, full_name, email}`).

5. **`CLAUDE.md`** — document under *Partner Institution: Experts & Course Roster*. One line in the endpoints list + a sentence on the service.

**No migration** — read-only feature over existing columns.

## Edge cases & rules

- **Removed experts.** `created_by` is `SET_NULL`; deactivation flips `affiliation_status='removed'` + `is_verified=False` but does **not** delete the user, so historical content still attributes correctly. Default the rollup to **active** affiliates (`affiliation_status='active'`); optionally accept `?include_removed=true` later.
- **Null author rows.** Pre-`AuthoredModel` content (created before migration `0016`) has `created_by = NULL`. These are silently excluded from per-expert counts. If a "legacy / unattributed" bucket is wanted, surface a separate `unattributed` count — out of scope for v1; note it so the numbers' not-summing-to-total is understood.
- **403 vs 404.** Slug-less endpoint on numeric IDs → any not-owned `course_id`/`expert_id` returns **404**, never 403 (don't leak existence of another institution's course/expert).
- **Cross-institution safety.** Every query is filtered by `partner_institution=institution` from the authenticated user — never from a client-supplied institution id.
- **Pagination.** Roster-sized (tens, not thousands) → return the full list unpaginated for v1. If large institutions appear, wrap with `StandardResultsSetPagination` over the experts list.

## Build order (suggested)

1. Lean core: service + view + url, **creates only**, no params. Returns full roster zero-filled.
2. Add `course_id` filter.
3. Add `expert_id` filter.
4. Add `include_edits` (edit counts + `last_edited_at`).

## Future extensions

- Per-course breakdown nested under each expert (`courses: [{course_id, title, lectures, quizzes, ...}]`).
- Date-range filter (`?since=&until=`) for "activity this quarter".
- Roll into a broader institution analytics dashboard (ties to the unbuilt SRS §7.7 reporting suite).
