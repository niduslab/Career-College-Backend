# Scheduled Courses (Cohort-Based Delivery)

**Status:** ✅ Implemented (all 3 phases, see §13). As-built reference: `docs/architecture/22-scheduled-courses.md`; manual-test guide: `docs/api-testing/postman-schedules.md`. This document is kept as the pre-build design rationale.
**Depends on:** existing `NidusCourse`, `Enrollment`, `CourseSection`/`SectionContent`, the course status state machine (`courses/all_models/course_models.py`), and the partner-institution roster model. No app is added; new model(s) live in `courses/`.
**Related:** `FEATURE_STATUS.md` (Partner Institution → Features Not Built) lists this as a wishlist item — this document supersedes that bullet list once implemented, and should replace it at that point.

---

## 1. Problem

Today every `NidusCourse` is self-paced and evergreen: fully authored before submission, frozen at `published`, accessible to enrolled learners forever. There is no way to run a course as a scheduled cohort — fixed enrollment window, fixed start date, content authored progressively week-by-week while the course is live and learners are already enrolled in it.

This plan defines a **scheduled delivery mode** layered on top of the existing course model, usable by both individual instructors and partner institutions.

## 2. Scope decisions (locked)

These were decided up front and constrain everything below:

| Question | Decision |
|---|---|
| What does "scheduled" mean? | Fixed enrollment window + fixed start date. Content is authored gradually (e.g. week 1 module uploaded in week 1, week 2 module uploaded in week 2) while the course stays `published` and accessible to already-enrolled learners. |
| Access after the schedule ends? | **Full lifetime access** — identical to today's self-paced behavior. An end date does not revoke content access. |
| Model shape? | A **new model wraps `NidusCourse`** (a `CourseSchedule`/cohort model), rather than adding schedule fields directly onto `NidusCourse`. The course stays the reusable curriculum template; the schedule holds dates + roster scoping. |

## 3. Current-state audit (why a wrapper model, not a field bolt-on)

Confirmed by reading the current models before designing:

- `NidusCourse` (`courses/all_models/course_models.py:106-386`) has no schedule-related fields at all — no `start_date`, `end_date`, or `enrollment_deadline`. The only date is `published_at`, set automatically by `save()` from `status`.
- `is_editable()` (`:250-253`) — `EDITABLE_STATUSES = frozenset(('draft', 'rejected'))`. Every other status, including `published`, is locked to edits. This directly conflicts with "upload week 2 content while the course is live and published" — a new carve-out is required (§6).
- `Enrollment` (`courses/all_models/enrollment_models.py`) has a single FK to `course` and a unique constraint on `(user, course)`. There is no cohort/batch grouping — structurally, a learner can only ever have one enrollment per course, ever.
- `CourseSection` / `SectionContent` have no release-date or unlock-date field of any kind — no drip-content infrastructure exists.
- `Webinar` (`webinars/all_models/webinar_models.py`) already has a single-event schedule shape (`scheduled_at`, `duration_minutes`, `max_capacity`, a 3-state `draft → published → archived` machine) that is a useful structural reference, but a webinar is one event, not a multi-week curriculum — it cannot be reused directly.
- Repo-wide search for `cohort`, `batch`, `intake`, `drip` returns zero hits. No partial implementation exists. The only prior trace is a wishlist bullet list in `FEATURE_STATUS.md` (course scheduling, staggered content release, schedule-based access control, on-track/behind status, cohort filtering in analytics) — none of it started.

Conclusion: the curriculum tree, approval flow, payments, and analytics infrastructure are all reusable as-is. Scheduling itself needs one new model plus three small, additive, nullable changes elsewhere. Nothing about this requires a new Django app.

## 4. New / changed models

### 4.1 `CourseSchedule` (new model, `courses/all_models/course_models.py`)

Wraps a `NidusCourse`. One course template can have many schedules over time (repeat cohorts) — no reason to cap it at one. Inherits `AuthoredModel` (`created_by` / `last_edited_by`) like other authored content.

```python
class CourseSchedule(AuthoredModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SCHEDULED = 'scheduled', 'Scheduled'
        ONGOING = 'ongoing', 'Ongoing'
        COMPLETED = 'completed', 'Completed'
        ARCHIVED = 'archived', 'Archived'

    course = models.ForeignKey(NidusCourse, on_delete=models.CASCADE, related_name='schedules')
    cohort_label = models.CharField(max_length=100, blank=True)   # e.g. "Fall 2026 Batch"
    timezone = models.CharField(max_length=64, default='UTC')      # mirrors Webinar.timezone
    enrollment_opens_at = models.DateTimeField()
    enrollment_closes_at = models.DateTimeField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)
    max_seats = models.PositiveIntegerField(null=True, blank=True)  # None = unlimited
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
```

State machine mirrors `Webinar`'s simple pattern, not `NidusCourse`'s 6-state one: `draft → scheduled → ongoing → completed`, with `archived` reachable from `completed` (and back to `draft` for rework, matching the project's existing archive/rework convention). A single `transition_to()` method, same convention as `NidusCourse` and `Webinar`.

### 4.2 `CourseSection.unlocks_at` (new nullable field)

```python
unlocks_at = models.DateTimeField(null=True, blank=True)
```

Section-level granularity (not per-lecture) — matches the "week 1 module / week 2 module" framing and keeps drip authoring coarse and simple. `NULL` means "unlocked immediately" (default, self-paced-compatible).

### 4.3 `Enrollment.schedule` (new nullable FK)

```python
schedule = models.ForeignKey(CourseSchedule, null=True, blank=True, on_delete=models.SET_NULL, related_name='enrollments')
```

Self-paced enrollments keep `schedule=NULL` — zero behavior change to the existing path.

The existing `unique_together(user, course)` must become two **partial unique constraints**:

- `(user, course) WHERE schedule IS NULL` — preserves today's one-enrollment-ever rule for self-paced.
- `(user, schedule) WHERE schedule IS NOT NULL` — one enrollment per learner per cohort, but allows the same learner to join a *different* schedule/cohort of the same course later (retake next term).

This is not a new pattern for the codebase — `Order` already uses partial uniques (`(user, course) WHERE status='paid'`, `(user, webinar) WHERE status='paid'`) and messaging already uses two partial unique constraints to handle a nullable-course pair. Follow that precedent exactly.

No other new models are needed. A separate `ScheduleEnrollment` through-table was considered and rejected — it would duplicate progress/certificate/watch-progress plumbing that is already keyed off `Enrollment`.

## 5. Institution vs. individual-instructor ownership

No new discriminator needed — `course.partner_institution_id` (nullable FK, already the sole discriminator used throughout the codebase) continues to decide ownership:

- **Institution-owned course:** `CourseSchedule` create/manage (dates, capacity, schedule-level publish/archive) is **institution-only**, mirroring the existing rule that `WebinarDetailView` PATCH is institution-only. Assigned experts (`course.instructors`) may upload weekly content into an ongoing schedule but may not touch schedule dates or roster — the same authoring/roster split already enforced for the course roster itself.
- **Individual-instructor course:** the instructor manages their own `CourseSchedule` directly, gated the same way course authoring already is (`IsVerifiedCourseCreator`).

No new permission classes are required — reuse `IsVerifiedPartnerInstitution`, `IsVerifiedCourseCreator`, and object-level ownership checks, following the same shape as `WebinarHostView` / `InstitutionCourseInstructorView`.

## 6. Content editing while a schedule is ongoing

Core conflict: `is_editable()` restricts edits to `draft`/`rejected`; a scheduled course needs content additions while `status=published`.

**Decision:** do not touch `EDITABLE_STATUSES` globally — self-paced courses keep today's lock-after-publish rule unchanged. Instead, `guard_editable()` (`courses/utils.py`) gains a narrow additional branch: if the course has a `CourseSchedule` with `status='ongoing'`, allow content operations (new section/lecture/quiz, and edits to already-unlocked content) even while the course itself is `published`. This keeps the blast radius of the change to one helper function, reused everywhere content-edit guards already run.

Authorship rule for who may add content mid-run is unchanged — the course's own `instructors` (including institution-provisioned experts) or `created_by`. No new permission class.

## 7. Approval gate

Two-part split:

1. **Initial gate is unchanged.** A course must pass the existing review flow before its first `CourseSchedule` can move to `scheduled`/`ongoing` — individual instructor: `draft → under_review → published`; institution course: `draft → institution_review → under_review → published`. `_validate_course_completeness()` still runs at that point, checked against whatever content exists at submission time (typically week 1), not the full multi-week curriculum.
2. **Weekly drip additions after publish do not go back through admin review.** Re-submitting every week's content for admin approval is impractical and inconsistent with the trust already placed in verified instructors/institution experts elsewhere in the codebase (`AuthoredModel` stamping, not a review gate, is how authorship is tracked). The same experts who could edit pre-publish simply continue authoring, now gated by `unlocks_at` visibility instead of a review step.

**Open decision, not resolved by this plan:** if admin oversight over post-publish content additions is wanted, the cheaper option is a passive audit surface (e.g. an analytics view: "content added after publish, by whom, when") rather than a blocking re-review gate, which would stall weekly releases. Revisit if required.

## 8. Enrollment flow

`enroll_learner()` (`courses/services/enrollment_service.py:263`) gains a keyword-only `schedule=None` parameter:

```python
enroll_learner(user, course, *, enrollment_type=Enrollment.EnrollmentType.FREE, allow_unpublished=False, schedule=None)
```

When `schedule` is passed:

- Enforce `schedule.enrollment_opens_at <= now <= schedule.enrollment_closes_at` → `422` outside the window.
- Capacity check reuses the webinar over-subscription fix: `CourseSchedule.objects.select_for_update()` on the schedule row **before** counting active enrollments against `max_seats`, so two concurrent first-time enrollees can't both pass the check.
- Sets `Enrollment.schedule = schedule`.

**Payments:** no structural change to `Order`. Price continues to live on `NidusCourse`, not per-schedule, so checkout still targets `course` exactly as today; `schedule_id` is threaded through the enrollment call after payment finalizes, not through `Order`. This avoids touching the existing "`Order` has exactly one of `course`/`webinar`" check constraint.

The self-paced path (`schedule=None`) is untouched — byte-for-byte identical behavior.

## 9. Learner access control

Three independent gates, all must pass for a learner to view a specific piece of content:

1. **Enrollment scope** — an active `Enrollment` row matching the course (and matching `schedule`, if the course has one).
2. **Schedule window** — `now >= schedule.start_date`. Before start, the learner is validly enrolled but the course isn't "live" yet.
3. **Section drip lock** — `section.unlocks_at is None or now >= section.unlocks_at`.

Status-code treatment (consistent with the project's existing 403/404-vs-422 split — this is a domain-timing rule, not an access-denied case):

- `LearnerCurriculumView` still lists locked sections (don't hide structure from an already-enrolled learner) but marks them `is_locked: true`.
- `LearnerLectureDetailView` / quiz / assignment detail on a locked item → **422** "Not yet released" — same family as other course-not-editable domain rules, not a 403/404.

Course owner/instructor bypass: identical to the existing preview bypass already implemented in `resolve_course_access` (`courses/services/learner_service.py`) — instructors/experts can always see their own content regardless of lock state, since they need to QA it before release.

## 10. Access after the schedule ends

Per the locked decision (§2): **full lifetime access**, identical to today's self-paced behavior. `schedule.end_date` does not revoke content access.

What `end_date` *does* do: flips `CourseSchedule.status → completed` (bookkeeping/analytics only). It doesn't gate anything learner-facing on its own, since `enrollment_closes_at` already closes new enrollments earlier in the lifecycle.

**Not decided by this plan, flagged for a follow-up call:** whether quiz/assignment/coding *submission* should stay open forever (matching viewing access) or cut off at `end_date` even though viewing stays open. Default recommendation is "stays open" — no new field, simplest, consistent with the lifetime-access decision. If a hard submission cutoff is wanted later, add a `submissions_close_at_end` boolean to `CourseSchedule` at that point; not included now.

Certificates and progress (`recalculate_progress()`, certificate auto-issue) are untouched — a schedule has no effect on that path.

## 11. Explicitly out of scope (deferred)

Carried over from `FEATURE_STATUS.md`'s wishlist, not part of this plan:

- **Cohort-based analytics filtering** — per-schedule enrollment trend splits. The `analytics` app aggregates per-course only today; adding a schedule dimension is additive and can follow once schedules exist.
- **"On track / behind / overdue" learner status** — needs a history of `unlocks_at` transitions vs. learner progress; no current infrastructure for it.
- **Content-release notifications** (e.g. `NEW_CONTENT_RELEASED` firing when a section's `unlocks_at` passes) — would need a Celery-beat scan task shaped like `reap_stale_processing_orders_task`, plus the standard 4-edit notification event wiring (`NotificationEventType`, a builder, `EVENT_TO_CATEGORY`, an email template). Worth doing, not required for the core feature.
- **Content versioning per cohort** ("which cohort saw which version") — explicitly contradicts the locked decision that content is one shared, progressively-unlocked curriculum rather than per-cohort snapshots.

## 12. Migration summary

All changes are additive and nullable — no backfill risk to existing self-paced data:

- New table: `CourseSchedule`.
- `CourseSection.unlocks_at` — nullable field add.
- `Enrollment.schedule` — nullable FK add, plus a constraint migration replacing `unique_together(user, course)` with the two partial unique indexes described in §4.3.

## 13. Execution phases

Each phase ships independently testable and independently deployable — no phase depends on a later one being done to be correct in production.

### Phase 1 — Data model + schedule management — ✅ Implemented

Goal: `CourseSchedule` exists, institutions/instructors can create and manage schedules, but nothing learner-facing changes yet.

**As-built notes** (decisions confirmed at implementation): PATCH is allowed while `draft` **or** `scheduled` (frozen once `ongoing`); `scheduled → ongoing` and `ongoing → completed` flip automatically via the Celery-beat task `advance_course_schedules_task` (every 5 min); `scheduled → draft` exists as a rework safety valve for premature activation. Shipped in `courses/all_models/schedule_models.py`, `courses/services/schedule_service.py`, `courses/all_serializers/schedule_serializers.py`, `courses/all_views/schedule_views.py`, migration `courses/0018`, tests `courses/all_tests/test_course_schedules.py`. Endpoints: `/<pk>/schedules/`, `/<pk>/schedules/<id>/`, `.../activate|archive|rework/`.

- Migrations (§12): `CourseSchedule` table, `CourseSection.unlocks_at`, `Enrollment.schedule` + partial unique constraints.
- `CourseSchedule` model + `transition_to()` state machine (§4.1): `draft → scheduled → ongoing → completed`, `completed ↔ archived`, `archived → draft`.
- Schedule CRUD endpoints + serializers + service (`courses/services/`, new `schedule_service.py`), ownership-gated per §5: institution-only for institution-owned courses, `IsVerifiedCourseCreator` for individual instructors.
- Admin/authoring-side surface only — no enrollment or content-gating logic yet.

Exit criteria: an instructor or institution can create/publish/archive a `CourseSchedule` against an existing course; permissions match §5; no learner-visible behavior change.

### Phase 2 — Enrollment + content authoring integration — ✅ Implemented

**As-built notes:** `POST /<slug>/enroll/` takes optional `{"schedule_id": N}`; window/status/capacity checks live in `enrollment_service._assert_schedule_enrollable` (`select_for_update` on the schedule row before seat counting). `guard_editable` carve-out: `published` + ≥1 `ongoing` schedule → editable. Section serializers expose `unlocks_at` for drip authoring.

Goal: learners can enroll into a specific schedule, and instructors/experts can author content into an `ongoing` schedule without violating today's lock-after-publish rule for self-paced courses.

- `enroll_learner()` gains `schedule=None` (§8): enrollment-window check, `select_for_update()` capacity check against `max_seats`, sets `Enrollment.schedule`.
- Checkout/enrollment endpoint threads `schedule_id` through after payment finalizes — no `Order` model change (§8).
- `guard_editable()` carve-out (§6): content ops allowed on a `published` course when it has an `ongoing` `CourseSchedule`.
- Approval-gate policy enforced as documented (§7): initial review unchanged; no re-review path added for drip additions (deliberately nothing to build here beyond *not* blocking it).

Exit criteria: a learner can enroll into a schedule inside its enrollment window (and is rejected outside it or at capacity); an expert can add a new section/lecture to an `ongoing` scheduled course that is `published`; self-paced courses are provably unaffected (existing tests unchanged).

### Phase 3 — Learner access control + drip release — ✅ Implemented

**As-built notes:** one gate function `assert_content_released(enrollment, section)` raising `ContentNotReleasedError` (422), wired into the four consumption loaders and the three inline-fetch write views; `load_learner_curriculum` marks `is_locked` + `unlocks_at` per section (never hides structure); instructor preview bypasses all gates; `end_date` revokes nothing.

Goal: enrolled learners see the schedule/drip timing rules correctly; course ends without revoking access.

- Section drip lock (§9): `LearnerCurriculumView` marks locked sections `is_locked: true`; `LearnerLectureDetailView` / quiz / assignment detail return `422` "Not yet released" for locked items.
- Schedule-window gate (§9): block content access before `schedule.start_date` even for enrolled learners.
- Instructor/expert bypass (§9): reuse `resolve_course_access` preview bypass so authors can QA locked content.
- Schedule-end handling (§10): `end_date` flips status to `completed`, confirm no access revocation anywhere in the learner path (regression tests against §2's lifetime-access decision).

Exit criteria: locked-section behavior matches §9 exactly (403/404/422 split respected); a learner retains full content access after a schedule's `end_date` passes; certificate/progress paths (§10) are unaffected end-to-end.

Deferred items in §11 are not part of any phase above — pick them up only after Phase 3 ships and only if separately prioritized.
