# Design Proposal — Two-Stage Submission for Institution-Owned Courses

> Status: **Implemented** (v3 design shipped; see `courses/all_views/status_views.py`, `courses/all_models/course_models.py`, tests in `courses/all_tests/test_institution_submission_flow.py`)
> Scope: Add an institution-review stage between an expert finishing a course and the platform admin reviewing it, for partner-institution-owned courses only. Individual-instructor courses are unchanged.
> Related: [11-course-lifecycle.md](../architecture/11-course-lifecycle.md), [18-partner-institutions.md](../architecture/18-partner-institutions.md), [13-multi-instructor-collaboration.md](../architecture/13-multi-instructor-collaboration.md)

---

## Changelog

| Version | Change |
|---------|--------|
| v3 (current) | Corrected all HTTP status codes against actual code behaviour (`guard_editable` → **422**, not 403). Collapsed the two institution actions onto **one** dedicated `/institution-review/` endpoint (`{action: submit \| send_back}`) — `/submit/` is left **untouched** (individual instructors only), removing the dual-caller overload. Role separation is now enforced by **queryset scope**, not by permission classes (`IsVerifiedCourseCreator` already includes institutions, so it can't discriminate). Replaced the `assert` ownership guard with a `ValidationError` raise. Added an explicit **status-code matrix** (§4). Renamed the new status to `institution_review` (names the reviewer; parallels `under_review`). |
| v2 | `ready_for_review` status; separate expert `/finish/` + institution `/submit/` (overloaded) + `/send-back/`. |
| v1 | Branched `CourseSubmitForReviewView` on `partner_institution_id`; `institution_review` status; `CourseInstitutionReviewView` with `{action: approve\|reject}`. |

---

## 1. Problem

Today every course — whether authored by an individual instructor or by an institution-onboarded expert — submits **straight to the platform admin**. An institution has no checkpoint to vet its own experts' work before it reaches the public marketplace under the institution's name.

Desired flow for institution-owned courses:

```
expert finishes the course → marks as ready → institution reviews → institution submits to admin
```

Individual-instructor courses must keep the direct-to-admin flow.

---

## 2. Current system analysis

### 2.1 State machine (`NidusCourse`, [course_models.py](../../courses/all_models/course_models.py))

```
draft ──submit──► under_review ──admin approve──► published ──archive──► archived
  ▲                    │                                                    │
  │             admin  │ reject                                             │
  │                    ▼                                                    │
  └──── rework ──── rejected                              draft ◄───restore─┘
```

```python
VALID_TRANSITIONS = {
    'draft':        ('under_review',),
    'under_review': ('published', 'rejected'),
    'rejected':     ('draft',),
    'published':    ('archived',),
    'archived':     ('draft',),
}
EDITABLE_STATUSES = frozenset(('draft', 'rejected'))
```

`transition_to(new_status, reviewer=None, rejection_reason='')` is the single entry point. Guards:
- Leaving `draft` (→ `under_review`) runs `_validate_course_completeness()` (title/description, ≥1 section, every section has content, all active videos `ready`, every quiz has a question with a correct answer).
- `rejected` requires a non-empty `rejection_reason`.
- `published` / `rejected` require a `reviewer` (admin).

### 2.2 Endpoints ([status_views.py](../../courses/all_views/status_views.py))

| View | Endpoint | Transition | Who | Notifies |
|------|----------|------------|-----|----------|
| `CourseSubmitForReviewView` | `POST {pk}/submit/` | `draft → under_review` | owner or assigned instructor (`Q(instructors) \| Q(created_by)`), `IsVerifiedCourseCreator` | admins (`COURSE_SUBMITTED`) |
| `CourseAdminReviewView` | `POST {pk}/review/` | `under_review → published\|rejected` | `IsPlatformAdmin` | instructors (`COURSE_APPROVED`/`COURSE_REJECTED`) |
| `CourseReworkView` | `POST {pk}/rework/` | `rejected → draft` | owner or instructor | — |
| `CourseArchiveView` / `CourseRestoreView` | `POST {pk}/archive/` `…/restore/` | `published → archived` / `archived → draft` | instructor or admin | — |

### 2.3 Code facts the design must respect

- **`guard_editable()` returns `422`** ([utils.py:5-17](../../courses/utils.py)) when a course is not in `draft`/`rejected`. Content edits on a frozen course are **422**, never 403.
- **`IsVerifiedCourseCreator` = `IsVerifiedInstructor` OR `IsVerifiedPartnerInstitution`** ([permissions.py:110-119](../../core/permissions.py)). A partner-institution user **passes** this permission. It therefore **cannot** be used to keep an institution out of an expert action — role separation must come from the **queryset scope**, not the permission class.
- **Experts are in `course.instructors`; the institution user is `created_by` and is never in `instructors`** (it can't author content, see [18](../architecture/18-partner-institutions.md)). So scoping by `instructors=request.user` cleanly selects "expert only," and scoping by `partner_institution=…` cleanly selects "institution only."
- **`transition_to` raises `ValidationError`** for bad transitions → views map `message_dict` → 400, plain string → 422. Guards must `raise ValidationError`, not `assert`.
- **`partner_institution`** (FK, system-set at creation) is the reliable ownership switch.

---

## 3. Recommended system

Add **one** new status (`institution_review`) and give each role a distinct verb on its **own** endpoint. `/submit/` keeps its exact current meaning and is left untouched.

```
INDIVIDUAL instructor course  (partner_institution = NULL)   — UNCHANGED
  draft ──expert /submit/──► under_review ──admin──► published / rejected

INSTITUTION course            (partner_institution set)
  draft ──expert /finish/──► institution_review ──institution /institution-review/ (submit)──► under_review ──admin──► published
                                    │                                                                  │
                          institution /institution-review/ (send_back, +reason)                  admin reject
                                    │                                                                  │
                                    ▼                                                                  ▼
                                rejected ◄───────────────────────────────────────────────────── rejected
                                    │
                            expert /rework/ → draft → /finish/ → institution_review …
```

> **Admin-rejection loop (Option A):** an admin-rejected institution course, reworked and re-finished, always re-enters `institution_review`. The institution re-vets before it reaches the admin again — every course the admin ever sees is institution-approved. A content-hash fast-path (Option B, §5) can be added later without breaking any transition.

### 3.1 New status

`institution_review` — between `draft` and `under_review`, reachable **only** by institution-owned courses. **Not editable** (content frozen while the institution reviews, mirroring `under_review`). Means "expert marked it complete; institution has not yet forwarded it to the admin."

### 3.2 Updated `VALID_TRANSITIONS`

```python
VALID_TRANSITIONS = {
    'draft':              ('under_review', 'institution_review'),
    'institution_review': ('under_review', 'rejected'),
    'under_review':       ('published', 'rejected'),
    'rejected':           ('draft',),
    'published':          ('archived',),
    'archived':           ('draft',),
}

EDITABLE_STATUSES = frozenset(('draft', 'rejected'))
# institution_review intentionally excluded — content frozen during institution review
```

Ownership guard in `transition_to()` when leaving `draft` — a **`ValidationError`**, never `assert`:

```python
if self.status == 'draft':
    if self.partner_institution_id and new_status == 'under_review':
        raise ValidationError(
            'Institution-owned courses go to institution review first; use /finish/.'
        )
    if not self.partner_institution_id and new_status == 'institution_review':
        raise ValidationError(
            'Only institution-owned courses use the institution-review stage.'
        )
```

`_validate_course_completeness()` runs on **both** draft exits (`→ under_review` and `→ institution_review`), so the institution always reviews a publishable course. No re-validation on `institution_review → under_review` (content was frozen).

### 3.3 Endpoints

`/submit/`, `/review/`, `/rework/`, `/archive/`, `/restore/` keep their current behaviour. Two additions, role-separated by **queryset scope**:

| View | Endpoint | Transition | Permission + scope |
|------|----------|------------|--------------------|
| **`CourseMarkFinishedView`** *(new)* | `POST {pk}/finish/` | `draft → institution_review` | `IsVerifiedCourseCreator`; scope `instructors=request.user` (expert-only — institution isn't in `instructors` → 404). Guard: `partner_institution_id` must be set, else **422**. Runs completeness check. Notifies institution (`COURSE_MARKED_FINISHED`). |
| **`CourseInstitutionReviewView`** *(new)* | `POST {pk}/institution-review/` | `institution_review → under_review` **or** `→ rejected` | `IsVerifiedPartnerInstitution`; scope `partner_institution=request.user.partner_institution_profile` (→ 404 on no-access). Body `{action: submit \| send_back, rejection_reason?}`. **422** if course not in `institution_review`. |
| `CourseSubmitForReviewView` | `POST {pk}/submit/` | `draft → under_review` | **Unchanged.** Still `IsVerifiedCourseCreator`, scope `Q(instructors)\|Q(created_by)`. For an institution course the `transition_to` ownership guard rejects `draft → under_review` → **422** ("use /finish/"). |
| `CourseAdminReviewView` | `POST {pk}/review/` | `under_review → published\|rejected` | **Unchanged** — admins only ever act on `under_review`. |
| `CourseReworkView` | `POST {pk}/rework/` | `rejected → draft` | **Unchanged** — expert reworks regardless of who rejected. |

`/institution-review/` dispatch:
- `action=submit` → `transition_to('under_review')` (no reviewer needed) → notify admins (`COURSE_SUBMITTED`).
- `action=send_back` → `rejection_reason` required → `transition_to('rejected', reviewer=request.user, rejection_reason=…)` → notify expert (`COURSE_SENT_BACK`).

Both institution actions share one permission class and one ownership scope; they differ only in target status — which is exactly why they belong on one endpoint rather than overloading `/submit/`.

### 3.4 Notifications (3-touch: enum + builder + category map)

| Event | Trigger | Recipient | New? |
|-------|---------|-----------|------|
| `COURSE_MARKED_FINISHED` | expert `/finish/` | institution | **new** |
| `COURSE_SUBMITTED` | institution `/institution-review/` `submit` | admins | reuse |
| `COURSE_SENT_BACK` | institution `/institution-review/` `send_back` | expert | **new** |
| `COURSE_APPROVED` / `COURSE_REJECTED` | admin `/review/` | expert | reuse |

### 3.5 Data / migration

- Add `CourseStatus.INSTITUTION_REVIEW`. `makemigrations courses` (choices change only — no data migration).
- Optional audit fields `finished_at` / `finished_by` (expert completion timestamp; useful for support/disputes). Distinct from the transient `reviewer` arg. Add at launch or in a follow-up — low cost either way.

---

## 4. Status-code matrix

The single biggest source of error in earlier drafts was guessing status codes. These follow the actual code and the project's 403-vs-404 / 400-vs-422 conventions.

| Scenario | Code | Why |
|----------|------|-----|
| Expert `/finish/` on a complete institution draft | **200** | `draft → institution_review` |
| Expert `/finish/`, course incomplete | **400** | completeness check → `errors` payload (`_validate_course_completeness` → `message_dict`) |
| Expert `/finish/` on an **individual** course (no `partner_institution`) | **422** | business-rule violation (not an institution course) |
| Institution user calls `/finish/` | **404** | not in `instructors` scope (existence not leaked on numeric pk) |
| Institution `/institution-review/` `submit` on an `institution_review` course | **200** | `institution_review → under_review`; admins notified |
| Institution `/institution-review/` `send_back` + reason | **200** | `institution_review → rejected`; expert notified |
| Institution `/institution-review/` `send_back` without reason | **400** | `rejection_reason` required → field error |
| Institution `/institution-review/` when course not in `institution_review` | **422** | invalid transition |
| Expert (or any non-institution user) calls `/institution-review/` | **403** | `IsVerifiedPartnerInstitution` denies (permission-class failure, before object lookup) |
| Institution A acts on institution B's course | **404** | `partner_institution` scope (existence not leaked) |
| Expert `/submit/` on an institution course | **422** | ownership guard rejects `draft → under_review` |
| Editing content while in `institution_review` | **422** | `guard_editable` (course frozen) |

---

## 5. Justification

**Why each role gets its own verb on its own endpoint.** In v1 `/submit/` silently routed to a different target based on ownership — one word, two meanings. v2 fixed the verb but overloaded `/submit/` to also carry the institution's forward action, which forces a single DRF view to juggle two permission regimes, two scopes, and two source states. v3 keeps `/submit/` exactly as-is (individual instructors), so there is **zero regression risk** on the existing path, and gives institutions a dedicated `/institution-review/`. Clear verbs: experts **finish**, institutions **review** (submit / send-back), admins **review/publish**.

**Why role separation is by queryset scope, not permission class.** `IsVerifiedCourseCreator` already returns true for partner institutions, so it cannot keep an institution out of `/finish/`. Scoping `/finish/` by `instructors=request.user` does the job structurally — the institution user simply isn't in `instructors`, so the row isn't found (404). Symmetrically, `/institution-review/` scopes by `partner_institution`, which experts never match. The permission classes gate *category* of user; the scope gates *which object*.

**Why one `/institution-review/` endpoint with an `action` instead of two.** Both institution actions share the same permission class (`IsVerifiedPartnerInstitution`) and the same ownership scope; they differ only in the target status and whether a reason is required. One endpoint with a two-value `action` keeps that shared setup in one place. (Two endpoints `/institution-submit/` + `/send-back/` are equally acceptable — the non-negotiable is that they are *institution-scoped*, not bolted onto the expert's `/submit/`.)

**Why reuse `rejected` + `/rework/` for send-back.** The expert recovery loop (`rejected → draft → finish → institution_review`) is identical whether the institution or the admin rejected. Reusing it avoids a second rejected status, a second rework endpoint, and duplicate notification wiring. `rejection_reason` already carries the message; `COURSE_SENT_BACK` distinguishes the institution's send-back from an admin rejection in the feed.

**Why `institution_review` is non-editable.** If the expert keeps editing after `/finish/`, the institution reviews a moving target. Freezing (same rule as `under_review`) makes "what the institution approved" equal "what the admin receives." Send-back returns the course to `rejected` (editable) so the expert can act on feedback.

**Why completeness runs only at draft-exit.** Running it on `draft → institution_review` guarantees the institution reviews a publishable course. The course is then frozen, so re-checking on `institution_review → under_review` can only return the same result.

**Why the admin stage is untouched.** Admins only ever see and act on `under_review`. The institution gate is upstream and invisible — no admin tooling, queries, or permissions change, and no risk to the individual-instructor path.

**Why `institution_review` (naming).** It names the actor and reads as the first of two review gates (`institution_review` → `under_review`/admin). `ready_for_review` (v2) describes the expert's intent but pairs confusingly with `under_review` (both "review", neither says *who*).

**Cost / blast radius.** One enum value, one migration, two new endpoints, **no change** to `/submit/`/`/review/`/`/rework/`, and two new notification events. Individual-instructor courses, the catalog, enrollment, and the admin flow are all untouched. `transition_to` stays the single mutation path.

---

## 6. Open questions

1. **Admin-rejection fast-path (Option B)** — if institutions report friction re-approving courses admin-rejected for non-content reasons, stamp a content hash at `institution_review` entry; on resubmit after an admin rejection with an unchanged hash, skip straight to `under_review`. Deferred post-launch.
2. **Audit fields** — add `finished_at` / `finished_by` at launch, or follow-up migration?
3. **SLA / stall protection** — no auto-escalation proposed. A Celery-beat job firing `COURSE_INSTITUTION_REVIEW_OVERDUE` to the institution (and optionally admins) after N days in `institution_review` would stop courses silently stalling. Recommended follow-up.

---

## 7. Implementation checklist (when approved)

- [ ] `CourseStatus.INSTITUTION_REVIEW` + updated `VALID_TRANSITIONS` + ownership guard (`raise ValidationError`, not `assert`) in `transition_to()` ([course_models.py](../../courses/all_models/course_models.py))
- [ ] `EDITABLE_STATUSES` — confirm `institution_review` excluded
- [ ] `CourseMarkFinishedView` + `POST {pk}/finish/` + URL; scope `instructors=request.user`; 422 if not institution-owned; completeness check via `transition_to`
- [ ] `CourseInstitutionReviewView` + `POST {pk}/institution-review/` + URL; `IsVerifiedPartnerInstitution`; scope `partner_institution`; `{action: submit|send_back}`; 422 if not in `institution_review`; reason required on `send_back`
- [ ] `CourseSubmitForReviewView` — **no code change** beyond confirming the ownership guard yields a clear 422 for institution courses (add an explicit message if the generic transition error is unclear)
- [ ] `COURSE_MARKED_FINISHED` + `COURSE_SENT_BACK`: enum + builder + category map
- [ ] Optional audit fields `finished_at` / `finished_by`
- [ ] `makemigrations courses`
- [ ] Tests (codes per §4):
  - [ ] Expert `/finish/` → `institution_review`; completeness check runs (incomplete → 400)
  - [ ] Content edit during `institution_review` → **422**
  - [ ] Institution `/institution-review/` `submit` → `under_review`; admins notified
  - [ ] Institution `/institution-review/` `send_back` + reason → `rejected`; expert notified; missing reason → 400
  - [ ] Expert `/submit/` on institution course → **422**
  - [ ] Institution user `/finish/` → **404** (scope)
  - [ ] Non-institution user `/institution-review/` → **403**
  - [ ] Expert `/finish/` on individual course → **422**
  - [ ] Institution A on institution B's `institution_review` course → **404**
  - [ ] Admin-rejected institution course: expert `/rework/` → `/finish/` → `institution_review` (not `under_review`)
  - [ ] Individual course flow unchanged (`draft → under_review` direct)
  - [ ] Admin flow unchanged (only ever acts on `under_review`)
- [ ] Docs: [11-course-lifecycle.md](../architecture/11-course-lifecycle.md), [18-partner-institutions.md](../architecture/18-partner-institutions.md), CLAUDE.md, README, Postman guide
