# 18) Partner Institutions

A **partner institution** is a `User(user_type='partner_institution')` that owns courses, onboards
its own teaching staff ("experts"), and assigns them to its courses. Four subsystems make this work:

1. **Institution verification** — a state machine that vouches for the institution's credentials
   (mirrors instructor identity verification). Approval flips `PartnerInstitutionProfile.is_verified`.
2. **Expert onboarding** — a verified institution auto-provisions instructor accounts; experts skip
   their own identity verification because the institution vouches for them.
3. **Departments** — institution-defined groupings an expert can be assigned to.
4. **Course creation + roster assignment** — institutions create courses through the same endpoint as
   instructors, then add their active experts to the instructor roster directly (no invite/accept).

The gate for everything except verification itself is the `IsVerifiedPartnerInstitution` permission:
`user_type == 'partner_institution'` **and** `PartnerInstitutionProfile.is_verified and is_active`.

## Key files

| File | Purpose |
|------|---------|
| `id_verification/models.py` | `InstitutionVerification` model, `VALID_TRANSITIONS`, `transition_to()`, `_mark_institution_verified()` |
| `id_verification/all_views/institution_views.py` | Institution-facing verification endpoints (create/update/submit/my-list/my-detail) |
| `id_verification/all_views/admin_views.py` | Admin institution-verification review endpoints |
| `authentication/models.py` | `PartnerInstitutionProfile`, `InstructorProfile` (affiliation fields), `Department` |
| `authentication/services/expert_service.py` | `provision_expert`, `update_expert`, `set_expert_active`, `ExpertError` |
| `authentication/services/department_service.py` | Department CRUD, `resolve_expert_department`, `DepartmentError` |
| `authentication/all_views/` | `InstitutionExpert*`, `InstitutionDepartment*` views |
| `courses/services/institution_course_service.py` | `add_course_instructor`, `remove_course_instructor`, `InstitutionCourseError` |
| `courses/all_views/institution_course_views.py` | `InstitutionCourseInstructorView` (roster add/remove) |
| `core/permissions.py` | `IsVerifiedPartnerInstitution`, `IsVerifiedCourseCreator` |

---

## Subsystem 1: Institution Verification

`InstitutionVerification` mirrors `IdentityVerification` (see [07-id-verification.md](07-id-verification.md))
but its FK is to `PartnerInstitutionProfile`, not `User`, and on approval it verifies the **institution
profile** instead of an instructor.

### State machine

```
                    submit                pick_up/review
  [draft] ────────────────► [submitted] ────────────────► [under_review]
     ▲                                                          │
     │                          ┌───────────────────────────────┼──────────────────┐
     │                          │ approve         │ reject       │ request_action
     │                          ▼                 ▼              ▼
     │                     [approved]        [rejected]   [action_required]
     │             (is_verified=True,                            │
     │              is_active=True)                       resubmit│
     │                                                           ▼
     └──────────────────────────────────────────────────── [submitted]
```

**No `expired` state** (unlike the instructor flow). Otherwise the lifecycle is identical.

```python
# id_verification/models.py — InstitutionVerification
VALID_TRANSITIONS = {
    'draft':           ('submitted',),
    'submitted':       ('under_review',),
    'under_review':    ('approved', 'rejected', 'action_required'),
    'action_required': ('submitted',),
}

REQUIRED_FOR_SUBMIT = ('registration_number', 'issuing_authority', 'accreditation_document')
```

### Model fields

| Field | Type | Notes |
|-------|------|-------|
| `institution` | FK → `PartnerInstitutionProfile` | `related_name='verifications'`, cascade |
| `registration_number` | CharField | **Required before submit** |
| `issuing_authority` | CharField | **Required before submit** (e.g. Ministry of Education) |
| `official_email` | EmailField | Optional institutional contact |
| `accreditation_document` | FileField | **Required before submit** (PDF/image) |
| `authorization_letter` | FileField (null) | Optional — letter authorizing the admin to act for the institution |
| `status` | CharField | Current state |
| `reviewed_by` / `reviewed_at` | FK → admin / datetime | Set on admin transitions |
| `rejection_reason` | TextField | Required when rejecting |
| `action_required_reason` | TextField | Required when requesting action |
| `admin_notes` | TextField | Internal — never exposed to the institution |
| `submitted_at` | datetime (null) | Set on each submit/resubmit |

### `clean()` guard

```python
def clean(self):
    if self.institution.user.user_type != 'partner_institution':
        raise ValidationError('Institution verification is only available for partner institutions.')
```

### Approval side-effect

```python
def _mark_institution_verified(self):
    profile = self.institution
    if not profile.is_verified: profile.is_verified = True
    if not profile.is_active:   profile.is_active   = True
    # saved with update_fields
```

`transition_to('approved')` calls this in-model, so the link between verification status and the
`IsVerifiedPartnerInstitution` gate holds regardless of which code path triggered the transition.

### Endpoints

Institution-facing — gated `IsAuthenticated + IsEmailVerified` **plus a `user_type == 'partner_institution'`
guard inside the view** (`_get_institution`). It is deliberately **not** `IsVerifiedPartnerInstitution`:
verification is the gate the institution is trying to clear, so requiring verification to access it would
be circular.

```
POST   /api/v1/verification/institution/create/        → create draft
PATCH  /api/v1/verification/institution/{id}/update/   → fill credential fields (draft/action_required only)
POST   /api/v1/verification/institution/{id}/submit/   → transition to 'submitted'
GET    /api/v1/verification/institution/my/            → list own verifications
GET    /api/v1/verification/institution/my/{id}/       → detail of own submission
```

Admin-facing — gated `IsPlatformAdmin`:

```
GET    /api/v1/verification/admin/institution/list/          → paginated list (filter ?status=)
GET    /api/v1/verification/admin/institution/{id}/          → full detail incl. admin_notes
POST   /api/v1/verification/admin/institution/{id}/review/   → approve / reject / request_action
```

`expire` is **not** a valid action for institutions → `422`.

### Notifications

Submit dispatches `INST_VERIFICATION_SUBMITTED` to admins; the admin decision dispatches one of
`INST_VERIFICATION_APPROVED` / `INST_VERIFICATION_REJECTED` / `INST_VERIFICATION_ACTION_REQ` back to the
institution — all via `transaction.on_commit` so a rolled-back transaction never emits a phantom
notification. (Event types in `notifications/models.py`, category `VERIFICATION`.)

---

## Subsystem 2: Expert Onboarding (auto-provision)

A verified institution onboards an expert via `expert_service.provision_expert()`. There is **no
self-registration and no OTP step** — the institution vouches for the expert:

```python
# authentication/services/expert_service.py
def provision_expert(institution_profile, *, full_name, email, bio='',
                     specialization=None, headline='', department_id=None):
    # 1. normalize + validate email is unique (incl. soft-deleted) → ExpertError(422)
    # 2. resolve_expert_department() — must be an active dept of THIS institution
    # 3. password = secrets.token_urlsafe(9)
    # 4. User.objects.create_user(user_type='instructor', is_email_verified=True, password=...)
    #    → profile signal fires, then _attach_to_institution(...)
    # 5. on_commit: send_expert_credentials_email_task(user_pk, password, institution_name)
    # 6. on_commit: dispatch(EXPERT_ONBOARDED, [user], skip_email=True)
```

`_attach_to_institution` sets on the auto-created `InstructorProfile`:

| Field | Value | Why |
|-------|-------|-----|
| `affiliated_institution` | the institution | links expert ↔ institution |
| `onboarding_source` | `'institution'` | distinguishes from self-registered instructors |
| `affiliation_status` | `'active'` | gates roster eligibility |
| `affiliated_at` | now | |
| `is_verified` | `True` | institution vouches — expert can author immediately, **skips** `IdentityVerification` |
| `department` | resolved `Department` or `None` | |

And on the `User`: `is_email_verified=True` — no OTP ownership proof required, loginable immediately.

### Why the password is kept out of the notification

The plaintext password is passed as a **Celery task arg** to `send_expert_credentials_email_task`
(emailed once, never persisted except as the hash on the `User`). The `EXPERT_ONBOARDED` dispatch uses
`skip_email=True` precisely so the password never lands in `Notification.data`.

### Deactivation re-locks authoring

```python
def set_expert_active(institution_profile, profile, active):
    profile.affiliation_status = 'active' if active else 'removed'
    profile.is_verified = active          # ← removed expert can no longer author
```

`is_verified` mirrors active state, so a removed expert immediately fails `IsVerifiedCourseCreator`.

### Endpoints

All gated `IsVerifiedPartnerInstitution`; every query scoped to the caller's own institution;
numeric-id detail → **404** on no-access (never leak existence).

```
GET/POST   /api/v1/auth/partner/experts/        → list / onboard expert
GET/PATCH  /api/v1/auth/partner/experts/{id}/   → detail / edit (bio, headline, specialization,
                                                   department_id, activate-deactivate)
```

`update_expert` uses an `_UNSET` sentinel for `department_id`: omit → leave untouched; `None` → clear;
an id → reassign (re-validated against the institution's own active departments).

---

## Subsystem 3: Departments

An institution defines its own departments; an expert's `InstructorProfile.department` points at one.

`Department` (`authentication/models.py`): owned by `PartnerInstitutionProfile` via `institution` FK;
`name` unique per institution **case-insensitively**; `is_active` boolean.
`InstructorProfile.department` is a nullable FK with `on_delete=SET_NULL` (rename-safe).

```python
# authentication/services/department_service.py
create_department(institution, name)        # blank → 400, duplicate (ci) → 422
rename_department(institution, dept, name)   # duplicate (ci, excluding self) → 422
set_department_active(dept, active)          # DELETE endpoint soft-deactivates
resolve_expert_department(institution, id)   # id must be an ACTIVE dept of this institution → else ExpertError(422)
```

**DELETE soft-deactivates** (`is_active=False`) rather than hard-deleting: assigned experts keep their
FK, and the department is excluded from the default (active-only) list.

### Endpoints

All gated `IsVerifiedPartnerInstitution`, scoped to the institution, numeric-id → 404.

```
GET/POST          /api/v1/auth/partner/departments/        → list (active) / create
GET/PATCH/DELETE  /api/v1/auth/partner/departments/{id}/   → detail / rename or toggle active / soft-deactivate
```

---

## Subsystem 4: Course Creation + Roster Assignment

### Creation

Partner institutions create courses through the **same** `CourseCreateAPIView` as instructors
(`IsVerifiedCourseCreator` = `IsVerifiedInstructor` OR `IsVerifiedPartnerInstitution`).

- `NidusCourseCreateUpdateSerializer.create()` sets `partner_institution` and **skips**
  `instructors.set([self])` for partner creators (partner-institution users are never in `instructors`).
- `NidusCourse.clean()` permits `created_by.user_type in ('instructor', 'partner_institution')` —
  **never narrow this back to instructor-only.**
- `partner_institution` is set once at creation by the system and is **never writable via the API**,
  not even by the owner (admin-only via Django admin). See [13-multi-instructor-collaboration.md](13-multi-instructor-collaboration.md).

### Roster assignment — direct add, no invite/accept

Distinct from the instructor `CourseInstructorInvite` flow (which requires an accept step). The
institution owns the roster and adds experts directly:

```python
# courses/services/institution_course_service.py
def add_course_instructor(course, institution_profile, expert_user_id):
    if course.partner_institution_id != institution_profile.id:  # → 404
    if not course.is_editable():                                 # → 422 (locked)
    expert_user = _get_active_expert_user(...)                   # must be ACTIVE affiliated expert → 422
    if course.instructors.filter(pk=expert_user.pk).exists():    # → 422 (already on roster)
    course.instructors.add(expert_user)
```

Only an **active affiliated expert of the owning institution** may be added, and only while the course
`is_editable()`. Assigned experts then edit content through the existing `CourseDetailView` / content
endpoints — `Q(instructors=user) | Q(created_by=user)` already covers them.

`InstitutionCourseError(message, http_status)` mirrors `InviteError` / `ExpertError`.

### Endpoints

Gated `IsVerifiedPartnerInstitution`; numeric pk → 404 on no-access.

```
POST    /api/v1/courses/{pk}/institution-instructors/                   → add expert (body: expert_user_id)
DELETE  /api/v1/courses/{pk}/institution-instructors/{expert_user_id}/  → remove expert
```

---

## End-to-end workflow

```
Institution                                 Admin
────────────────────────────────────────────────────────────────────────
register (user_type=partner_institution), verify email
POST /verification/institution/create/      → draft
PATCH .../{id}/update/                       → registration_number, issuing_authority, accreditation_document
POST  .../{id}/submit/                       → submitted   ──► INST_VERIFICATION_SUBMITTED to admins
                                                                 │
                                            POST /admin/institution/{id}/review/ {approve}
                                                                 │  → approved
                                                                 │  → PartnerInstitutionProfile.is_verified=True
       ◄── INST_VERIFICATION_APPROVED ───────────────────────────┘
Now IsVerifiedPartnerInstitution passes
       │
POST /auth/partner/departments/             → define departments
POST /auth/partner/experts/                 → onboard expert (account + emailed credentials)
       │                                        expert: is_verified=True, is_email_verified=True
POST /courses/create/                       → course (partner_institution set, instructors empty)
   ... add sections / content ...
POST /courses/{id}/institution-instructors/ → assign active expert to the roster
       │                                        expert can now edit content via CourseDetailView
POST /courses/{id}/submit/                  → under_review (same lifecycle as instructor courses)
```

---

## Why this design

- **Verification is a separate state machine, not a boolean** — same rationale as instructor identity
  verification: prevents skipped review steps and records the audit trail (`reviewed_by`, reasons).
- **Institution-onboarded experts skip their own identity verification** — the institution has already
  cleared admin review and vouches for its staff; forcing each expert through OTP + identity docs would
  duplicate that trust check. `is_verified=True` is set at provision time and re-locked on deactivation.
- **Direct roster add (no invite/accept)** — the institution *owns* the course and *created* the expert
  account, so there is no second party whose consent is needed. This is why it is deliberately kept
  distinct from the peer instructor-invite flow.
- **Soft-deactivation everywhere** (experts, departments) — preserves history and FK integrity; a
  removed expert keeps their authored content and a deactivated department keeps its assigned experts.
- **`IsVerifiedPartnerInstitution` is the single gate** for experts/departments/roster, but verification
  endpoints intentionally use a lighter `user_type` guard so the institution can reach the flow that
  grants verification in the first place.
