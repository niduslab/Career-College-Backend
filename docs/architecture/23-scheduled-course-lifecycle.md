# 23 — Scheduled Course Lifecycle (User Journey + Backend Flow)

This doc walks the **entire lifecycle of a scheduled (cohort-based) course** as a sequence of user actions, paired with what actually happens in the backend at each step. It complements [22-scheduled-courses.md](22-scheduled-courses.md) (data model, state machine reference) — this doc is the narrative/API-call version.

All endpoints below are prefixed `/api/v1/courses/`.

---

## 1. Instructor/expert creates the course as "Scheduled"

**User action:** Instructor picks "Scheduled (Cohort-Based)" instead of "Self-Paced" when creating a course, then fills in title, description, price, thumbnail, language, level, category, learning objectives, prerequisites, audiences. (`course_outline` — see step 2 — is typically filled in next, not necessarily at creation time.)

**API:** `POST /create/` → `CourseCreateAPIView` / `NidusCourseCreateUpdateSerializer`

**Backend:**
- `NidusCourse.delivery_mode` is set to `scheduled` at creation and is **immutable afterward** — `validate_delivery_mode()` on the update serializer rejects any later change.
- Course starts life in `status=draft`, same as any course.
- Partner-institution-owned courses set `partner_institution`; individual-instructor courses set `created_by` + `instructors=[self]` (unchanged from the non-scheduled path).

---

## 2. Instructor writes the course outline — sections are optional

**User action:** Instructor fills in `course_outline` — a plain text field describing the full topic/week-by-week plan (e.g. "Week 1: Intro\nWeek 2: Advanced Topics\n..."), typically as part of the same `PATCH /<pk>/` metadata call as title/description/etc. Instructor **may** also add real sections/content (`POST /<course_id>/sections/create/`, `/contents/`) if some is already built, but for a scheduled course this is entirely optional — the outline text is what stands in for a curriculum at submission time.

**API:** `PATCH /<pk>/` → `CourseDetailView` / `NidusCourseCreateUpdateSerializer` (field `course_outline`, normalized like `learning_objectives`/`prerequisites`/`audiences` — blank lines stripped)

**Backend:** No special gate on sections/content creation — identical to self-paced if the instructor chooses to add any. The relaxation is at submission-completeness-check time (step 4): a scheduled course does not need any `CourseSection` rows at all, only a non-blank `course_outline`.

---

## 3. Instructor attaches a schedule (the cohort)

**User action:** Instructor fills in cohort details — label ("Fall 2026 Batch"), timezone, enrollment window, start/end dates, seat cap.

**API:** `POST /<pk>/schedules/` → `CourseScheduleListCreateView.post` / `CourseScheduleCreateUpdateSerializer`

Body:
```json
{
  "cohort_label": "Fall 2026 Batch",
  "timezone": "Asia/Dhaka",
  "enrollment_opens_at": "2026-08-01T00:00:00Z",
  "enrollment_closes_at": "2026-08-25T00:00:00Z",
  "start_date": "2026-09-01T00:00:00Z",
  "end_date": "2026-12-01T00:00:00Z",
  "max_seats": 100
}
```

**Backend:**
- Ownership check: institution-owned course → institution-only; individual course → creator-only (`get_course_for_schedule_manage`).
- `CourseScheduleCreateUpdateSerializer.validate()` enforces date ordering: opens < closes, closes ≤ start, end > start.
- Row created with `status=draft` (`CourseSchedule.Status.DRAFT`) — a schedule in `draft` is invisible to learners and not yet "live."
- `save_authored()` stamps `created_by`/`last_edited_by`.
- One course can have **many schedules** (repeat cohorts) — this call can be made again later for "Spring 2027 Batch" etc.

At this point nothing is submitted for review yet. The instructor can keep patching the schedule (`PATCH /<pk>/schedules/<schedule_id>/`) or delete it (`DELETE`, draft-only) freely.

---

## 4. Instructor submits the course for review

**User action:** One submit button.

**API:**
- Individual-instructor course → `POST /<pk>/submit/` (`CourseSubmitForReviewView`) → `draft → under_review` directly.
- Institution-owned course → `POST /<pk>/finish/` (`CourseMarkFinishedView`) → `draft → institution_review` (goes to the institution first, which then forwards via `POST /<pk>/institution-review/` with `{"action": "submit"}` → `under_review`, or sends it back with `{"action": "send_back", "rejection_reason": "..."}`).

**Backend — `_validate_course_completeness()` (`courses/all_models/course_models.py`), the single completeness gate for both paths:**

| Check | Self-paced course | Scheduled course |
|---|---|---|
| Title/description present | required | required |
| ≥1 section, every section has content | **required** — zero sections or any empty section blocks submission | **not checked at all** — a scheduled course may submit with zero `CourseSection` rows |
| `course_outline` non-blank | not checked | **required** — `errors['course_outline']` if blank |
| Videos fully transcoded | required (whatever content exists) | required (whatever content exists) |
| Quizzes have questions + correct answers | required (whatever quizzes exist) | required (whatever quizzes exist) |
| At least one `CourseSchedule` attached | n/a | **required** — `errors['schedules']` if none exist |
| Schedule dates structurally sane | n/a | `schedule.date_logic_errors()` run per schedule (opens<closes, closes≤start, end>start) — this check **skips the "must be in the future" rule**, since review can sit for a while between submission and admin approval |

This is the meaningful relaxation for scheduled courses: a cohort course can go to review with **no curriculum built at all**, as long as it has a written `course_outline` and a schedule with sane dates attached. Sections/content may still be added if the instructor has some ready (drip release, step 10, still applies to whatever gets added later) — they're just never required at submission. A scheduled course with a blank `course_outline`, or with no schedule, or a self-paced course with zero/empty sections, is rejected with a `400` + field errors (`message_dict` → 400, not 422 — this is a validation-shaped rejection).

---

## 5. Admin reviews — now with cohort context

**User action:** Admin opens the review screen for the pending course. Instead of only seeing metadata, the admin now also sees: is this a scheduled course, what are its schedule(s) dates/seat caps, the full `course_outline` text the instructor wrote, and — if any real sections/content exist yet — how many sections actually have content vs. how many are still bare titles.

**API:** `GET /<pk>/review/` → `CourseAdminReviewView.get` → `CourseAdminReviewDetailSerializer`

Response includes the normal course fields (including `course_outline`) **plus**:
```json
{
  "delivery_mode": "scheduled",
  "course_outline": "Week 1: Intro\nWeek 2: Advanced Topics\n...",
  "schedules": [ /* full CourseScheduleSerializer list */ ],
  "outline_stats": {
    "total_sections": 0,
    "sections_with_content": 0,
    "empty_section_titles": []
  }
}
```

**Backend:** `NidusCourse.content_outline_stats()` walks `self.sections.all()` and reports which section titles (if any exist) have zero `SectionContent` rows — for a scheduled course submitted with no curriculum built at all, this is just all-zero and uninformative on its own. The admin's actual judgment call for a scheduled course rests on the written `course_outline` text (is this a believable plan for a paid N-week commitment?) plus whatever real sections/content the instructor chose to front-load; `outline_stats` remains useful whenever some curriculum does exist.

The admin then decides via the existing action endpoint:

**API:** `POST /<pk>/review/` with `{"action": "approve"}` or `{"action": "reject", "rejection_reason": "..."}`

---

## 6. Admin approves → course publishes, schedule activates itself

**User action:** Admin clicks approve. Nothing further required from the instructor.

**API:** `POST /<pk>/review/` `{"action": "approve"}` → `CourseAdminReviewView.post`

**Backend, in order:**
1. `course.transition_to('published', reviewer=admin)` — same as any course approval.
2. **New step:** for every schedule on the course still sitting in `draft`, the view calls `activate_schedule(schedule, admin)` → `schedule.transition_to('scheduled')`, which runs `_validate_activation()` (course must be published — just became true — dates must still be structurally sane **and** in the future).
3. **Two outcomes per schedule:**
   - **Dates still valid and future** → schedule flips to `scheduled` automatically. Instructor does nothing further; the cohort is live.
   - **Dates went stale while sitting in review** (e.g. `enrollment_closes_at` or `start_date` slipped into the past) → activation raises `ValidationError`, caught per-schedule (`try/except` around each row so one bad schedule doesn't block others), and the course **still publishes**. The instructor gets notified: `COURSE_SCHEDULE_NEEDS_ATTENTION` (in-app + email, `notifications/services/builders.py` → `_course_schedule_needs_attention`), listing the affected schedule labels, telling them to fix the dates and activate manually via `POST /<pk>/schedules/<schedule_id>/activate/`.
4. Notification recipient for the schedule-nudge: the owning institution's user if institution-owned, otherwise the course's instructors.

**Key guarantee:** publishing the course is never blocked by a stale schedule — the two concerns are decoupled on purpose.

---

## 7. Enrollment window opens

**User action:** Learner browses the catalog, sees the course is published, and — because it's a scheduled course — sees the available cohort(s) and picks one (or the course exposes only the currently-open cohort).

**API:** `POST /<slug>/enroll/` with `{"schedule_id": <id>}` (`CourseEnrollView`) — omitting `schedule_id` still works for a self-paced enrollment path; passing it enrolls into that specific cohort.

**Backend (`enroll_learner(..., schedule=schedule)`):**
- Schedule must be `status=scheduled`.
- `now` must be within `[enrollment_opens_at, enrollment_closes_at]` — outside the window → `422`.
- If `max_seats` is set: `CourseSchedule.objects.select_for_update()` locks the schedule row **before** counting existing active enrollments, so two learners racing for the last seat can't both get in — the second sees `422` (full).
- Paid-course gate runs first and is unaffected by scheduling — the learner pays once for the course; `Order` has no knowledge of schedules.
- `Enrollment.schedule` is set (nullable FK). Uniqueness allows a learner to hold both a self-paced row and a cohort row, or re-enroll in a later cohort of the same course (partial unique indexes on `(user, course) WHERE schedule IS NULL` and `(user, schedule) WHERE schedule IS NOT NULL`).

---

## 8. The cohort starts

**User action:** Nothing — this is fully automatic on the `start_date`.

**Backend:** Celery beat task `advance_course_schedules_task` runs every 5 minutes, and for every schedule past its `start_date` calls `schedule.transition_to('ongoing')` (per-row try/except — one bad row logs an error but doesn't stop the others; **no info-level logs in schedule code**, only error/exception).

**Before this moment:** enrolled learners can already see the full curriculum outline (via `GET /learn/<slug>/curriculum/`), but any section with a future `unlocks_at` (or, more fundamentally, the whole course if `now < schedule.start_date`) is locked. `assert_content_released()` (`courses/services/learner_service.py`) is the single gate function used everywhere content is read or written — it raises `ContentNotReleasedError` (422, not 403/404 — this is a *timing* rule, not an access rule) with:
- `"This course has not started yet."` if the cohort hasn't started, or
- `"This content has not been released yet."` if the individual section's `unlocks_at` is still in the future.

`load_learner_curriculum` never blocks on this — it lists every section with `is_locked: true/false` + `unlocks_at` so the learner sees the whole roadmap, just greyed out.

---

## 9. Instructor keeps adding material while the cohort is running

**User action:** Instructor uploads next week's lecture, adds a quiz, etc. — no new admin review requested or required.

**Backend:** `guard_editable(course)` (`courses/utils.py`) has one narrow carve-out for this: a `published` course whose schedule is `ongoing` stays content-editable. This closes again once the schedule reaches `completed`. There's no re-review step for drip content — `AuthoredModel`'s `created_by`/`last_edited_by` stamping is the audit trail, and the admin already saw this coming (step 5's `outline_stats`) when they approved.

Each new section/content the instructor adds can set its own `unlocks_at` — this is how "coming out next Monday" content gets scheduled without another submit/approve round-trip.

---

## 10. Content unlocks section-by-section

**User action:** Learner keeps visiting the curriculum page. Locked sections show a "coming on this date" note; opening a still-locked lecture/quiz/assignment/coding exercise directly returns a clear 422, not a confusing 403/404.

**Backend:** Same `assert_content_released()` gate, wired into all four consumption loaders (lecture, quiz, assignment, coding exercise) **and** the write endpoints (progress update, quiz submit, assignment submit, coding run/submit) — locked content blocks reads and writes alike. Instructor preview bypasses every gate (`enrollment is None` short-circuits to a no-op).

---

## 11. The cohort ends

**User action:** Nothing, again automatic on `end_date`. Enrolled learners keep everything.

**Backend:** `advance_course_schedules_task` flips `ongoing → completed` once `now > end_date`. This is bookkeeping only:
- New enrollments into this schedule stop (it's no longer `scheduled`).
- `guard_editable`'s ongoing-carve-out no longer applies, so instructor edits freeze again.
- Nothing is revoked from learners already in — no re-lock, no content removal, certificates/progress stay exactly as earned. A `null` `end_date` means the schedule just never reaches `completed` on its own (open-ended).

Manual admin/institution path: `POST /<pk>/schedules/<schedule_id>/archive/` (`completed → archived`) and `POST /<pk>/schedules/<schedule_id>/rework/` (`archived → draft`, or pulls back a premature `scheduled → draft` activation) exist for cleanup/undo, alongside the automatic transitions.

---

## 12. Reuse for the next run

**User action:** Instructor wants to run the same course again next term.

**API:** `POST /<pk>/schedules/` again — same course, new cohort.

**Backend:** No course duplication. `CourseSchedule` is a thin wrapper (`course.schedules` — one course, many schedules over time). The new schedule starts at `draft`, goes through its own `POST .../activate/` (or waits for another approval cycle if the course itself needed changes first), and gets its own independent enrollment window / learner roster.

---

## Full state-machine cheat sheet

**Course status** (unaffected by `delivery_mode` except at the completeness-check step):
`draft → institution_review|under_review → published → archived → draft`

**Schedule status** (`CourseSchedule.transition_to`):
```
draft --(admin approves course, or manual /activate/)--> scheduled
scheduled --(start_date reached, automatic)--> ongoing
scheduled --(manual /rework/)--> draft
ongoing --(end_date reached, automatic)--> completed
completed --(manual /archive/)--> archived
archived --(manual /rework/)--> draft
```

## API quick reference

| Step | Method + path | View |
|---|---|---|
| Create scheduled course | `POST /create/` | `CourseCreateAPIView` |
| Add section | `POST /<course_id>/sections/create/` | `CourseSectionCreateAPIView` |
| Attach schedule | `POST /<pk>/schedules/` | `CourseScheduleListCreateView` |
| Edit schedule | `PATCH /<pk>/schedules/<schedule_id>/` | `CourseScheduleDetailView` |
| Delete schedule (draft-only) | `DELETE /<pk>/schedules/<schedule_id>/` | `CourseScheduleDetailView` |
| Submit (individual) | `POST /<pk>/submit/` | `CourseSubmitForReviewView` |
| Submit (institution expert) | `POST /<pk>/finish/` | `CourseMarkFinishedView` |
| Institution forwards/sends back | `POST /<pk>/institution-review/` | `CourseInstitutionReviewView` |
| Admin views review context | `GET /<pk>/review/` | `CourseAdminReviewView` |
| Admin approves/rejects | `POST /<pk>/review/` | `CourseAdminReviewView` |
| Manual schedule activate | `POST /<pk>/schedules/<schedule_id>/activate/` | `CourseScheduleActivateView` |
| Manual schedule archive | `POST /<pk>/schedules/<schedule_id>/archive/` | `CourseScheduleArchiveView` |
| Manual schedule rework | `POST /<pk>/schedules/<schedule_id>/rework/` | `CourseScheduleReworkView` |
| Learner enrolls into a cohort | `POST /<slug>/enroll/` `{"schedule_id": N}` | `CourseEnrollView` |
| Learner views curriculum (locks shown) | `GET /learn/<slug>/curriculum/` | `LearnerCurriculumView` |
| Learner opens lecture/quiz/assignment/coding | `GET /learn/.../<id>/` | respective `Learner*DetailView` |

## Related docs
- [22-scheduled-courses.md](22-scheduled-courses.md) — data model, state machine, ownership rules
- `docs/api-testing/postman-schedules.md` — manual test walkthrough
- [CLAUDE.md](../../CLAUDE.md) → *Scheduled Courses (Cohorts)* section — condensed architectural reference
