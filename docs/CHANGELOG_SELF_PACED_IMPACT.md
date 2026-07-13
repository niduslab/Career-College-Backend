# Changelog — Self-Paced Course Impact from Scheduled Courses

Scheduled (cohort) delivery was layered onto the existing course model. Most of it is
additive and invisible to a plain self-paced course, but a few changes touch the
**self-paced path itself** — the pre-existing default every course used before cohorts
existed. This doc records only those ripple effects, so anyone maintaining self-paced
behavior knows what shifted underneath them.

For the cohort feature end-to-end see `docs/architecture/22-scheduled-courses.md` and
`docs/architecture/23-scheduled-course-lifecycle.md`. For the file-by-file build log see
`docs/CHANGELOG_SCHEDULED_COURSES.md`.

Legend: **CHANGED** = self-paced behavior is different now · **NEW-CONSTRAINT** = a new
rule now applies to self-paced courses · **UNCHANGED** = called out because you might
expect it to have changed, but it didn't.

---

## 1. New `delivery_mode` field — every course is now explicitly typed  · NEW-CONSTRAINT

`NidusCourse.delivery_mode` (`self_paced` | `scheduled`) was added, **default `self_paced`**.
Every pre-existing course and every course created without specifying a mode is `self_paced`
— behavior identical to before.

Two new rules that apply even if you only ever touch self-paced courses:

- **Immutable after creation.** `NidusCourseCreateUpdateSerializer.validate_delivery_mode()`
  rejects any change to `delivery_mode` on update (400). You cannot flip a self-paced course
  to scheduled (or vice versa) later — create a new course.
- Exposed read-only on `NidusCourseSerializer`, the catalog detail serializer, and the
  my-courses meta serializer; writable only at create.

---

## 2. Submission completeness is now branched by mode  · UNCHANGED (for self-paced)

`_validate_course_completeness()` (`courses/all_models/course_models.py`) split into a
mode-dependent curriculum check. The **self-paced branch is the historical rule, verbatim**:

- ≥1 section required, and every section must have content (`empty_section_titles`).
- Video/quiz completeness checks unchanged.
- `course_outline` (see §3) is **not** required for self-paced.

No self-paced submission that passed before fails now. The change is purely that the check
now has an `if delivery_mode == SELF_PACED:` guard around the section rules; the scheduled
branch is the new code.

---

## 3. New `course_outline` field — optional for self-paced  · CHANGED (additive)

`NidusCourse.course_outline` (plain `TextField`, blank by default) was added. It's **required
before submission for scheduled courses only**; for self-paced it is optional and unused by
the completeness check. It is now exposed read-only on the course, catalog-detail, and
my-courses serializers, and is writable on create/update (normalized like
`learning_objectives`/`prerequisites`/`audiences`). A self-paced course may set it for
marketing copy, but nothing requires or enforces it.

---

## 4. New `CourseSection.unlocks_at` — drip lock now applies to self-paced learners too  · CHANGED

This is the one genuine runtime behavior change for self-paced courses.

`CourseSection.unlocks_at` (nullable datetime) was added. **`NULL` = released immediately**,
which is the default and matches every pre-existing section, so untouched self-paced courses
behave exactly as before.

But the release gate is **not** cohort-only. `assert_content_released(enrollment, section)`
(`courses/services/learner_service.py`) has two independent gates, and the second one fires
for **every** learner regardless of enrollment type:

```python
# gate 2 — runs even when enrollment.schedule is None (self-paced)
if section.unlocks_at is not None and section.unlocks_at > now:
    raise ContentNotReleasedError('This content has not been released yet.')  # 422
```

Consequence: if an instructor sets a future `unlocks_at` on a section of a **self-paced**
course, self-paced learners hitting that section's lecture/quiz/assignment/coding detail or
its progress/submit write endpoints get **422 "This content has not been released yet."** —
reads *and* writes blocked until the timestamp passes. The curriculum endpoint still lists the
section (marked `is_locked: true` + `unlocks_at`), never hides it. Instructor preview
(`enrollment is None`) bypasses the gate.

Section create/update serializers now accept `unlocks_at`, so this is settable through the
normal authoring endpoints with no new endpoint.

---

## 5. `Enrollment` unique-constraint swap  · UNCHANGED (for self-paced) / migration note

The old single `unique(user, course)` on `Enrollment` was replaced by two partial uniques:

| Constraint | Condition | Rule |
|---|---|---|
| `uniq_enrollment_user_course_selfpaced` | `WHERE schedule IS NULL` | one self-paced enrollment per course, ever — **the exact old rule** |
| `uniq_enrollment_user_schedule` | `WHERE schedule IS NOT NULL` | one enrollment per cohort |

Self-paced dedup semantics are preserved byte-for-byte; only the constraint's **name and
mechanism** changed (single → partial). Relevant if any code or ops tooling referenced the old
constraint name. A learner may now hold a self-paced row *and* a cohort row for the same
course simultaneously.

---

## 6. `resolve_course_access` prefers the self-paced row  · UNCHANGED (for pure self-paced)

`resolve_course_access(user, course)` now orders a learner's active enrollments **schedule-
nulls-first**, so when a learner holds both a self-paced and a cohort enrollment, the
self-paced one wins (it's the most permissive — no release-timeline gates). A learner with
only a self-paced enrollment is unaffected: there's one row, it's still returned.

---

## 7. `enroll_learner()` signature grew two keywords  · UNCHANGED (for self-paced callers)

`enroll_learner(user, course, *, enrollment_type=..., allow_unpublished=..., schedule=None, via_payment=False)`.
Both new keywords default to the self-paced behavior:

- `schedule=None` → classic self-paced enrollment, unchanged.
- `via_payment=False` → normal gating; only the payment-finalize path passes `True`, and it
  only relaxes cohort gates (irrelevant when `schedule is None`).

Every existing self-paced call site (`CourseEnrollView` for a free course, etc.) is
byte-identical.

---

## 8. Payments: `Order.schedule` + paid-uniqueness swap  · UNCHANGED (for self-paced) / API-additive

- `Order.schedule` (nullable FK) added; `NULL` = self-paced purchase, unchanged.
- Paid uniqueness swapped, mirroring §5: self-paced course purchase is now
  `uniq_paid_order_user_course_selfpaced` (`WHERE status='paid' AND schedule IS NULL`) —
  same "one paid order per course" rule, new constraint name.
- `POST /payments/checkout/` response and the order serializer gained a `schedule_id` field;
  it is **`null`** for every self-paced course order. Purely additive to the response
  contract — existing clients ignoring the field are unaffected.
- A self-paced course checkout that omits `schedule_id` is byte-identical to before.

---

## 9. `guard_editable()` — self-paced editing rule preserved  · UNCHANGED

`guard_editable(course, section=None)` gained a carve-out that keeps a **published** course
content-editable while it has a `scheduled`/`ongoing` cohort. This carve-out is gated on the
course having a live schedule, so a **self-paced course (which can have none — see §10) never
enters it**: self-paced courses keep the historical lock-after-publish rule (editable only in
`draft`/`rejected`). Explicitly called out because the function signature changed — the
`section=` argument is only consulted inside the cohort carve-out and has no effect on
self-paced courses.

---

## 10. Cohorts cannot attach to a self-paced course  · NEW-CONSTRAINT

`assert_course_supports_schedules(course)` (`courses/services/schedule_service.py`) gates
`POST /<pk>/schedules/`: attaching a `CourseSchedule` to a `delivery_mode=self_paced` course
returns **422 "Schedules can only be added to scheduled (cohort-based) courses."** This is a
new refusal on self-paced courses — they were never *supposed* to carry cohorts, but nothing
enforced it before. Create-time is the only enforcement point (checkout/enroll never re-check
`delivery_mode`).

---

## Summary — what a self-paced-only operator must actually watch

Only two items change observable behavior for a course that never touches cohorts:

1. **§4** — setting `unlocks_at` on a section now drip-locks self-paced learners (422 until
   the timestamp passes). Previously no such gate existed for self-paced.
2. **§10** — you can no longer attach a schedule to a self-paced course (422).

Everything else is additive (new nullable fields defaulting to old behavior, new optional
keyword args, an additive `schedule_id: null` in payment responses) or a constraint
**rename** with identical semantics (§5, §8). No pre-existing self-paced flow breaks.
