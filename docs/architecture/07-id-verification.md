# 07) Identity Verification

Instructor identity verification is a state-machine workflow. An instructor cannot publish courses
until their identity is approved by an admin. Approval sets `InstructorProfile.is_verified = True`,
which enables the `IsVerifiedInstructor` permission class.

## Key files

| File | Purpose |
|------|---------|
| `id_verification/models.py` | `IdentityVerification` model, `VALID_TRANSITIONS`, `transition_to()` |
| `id_verification/all_views/instructor_views.py` | Draft, update, submit, my-list, my-detail endpoints |
| `id_verification/all_views/admin_views.py` | Admin list, detail, review endpoints |
| `id_verification/serializers.py` | Payload validation for create/update/submit |
| `id_verification/utils.py` | Upload path helpers |
| `core/permissions.py` | `IsPlatformAdmin`, `IsVerifiedInstructor` |

---

## State machine diagram

```
                    submit
  [draft] ─────────────────────────► [submitted]
     ▲                                     │
     │                              pick_up│
     │                                     ▼
     │                              [under_review]
     │                                     │
     │                    ┌────────────────┼──────────────────┐
     │                    │ approve        │ reject            │ request_action
     │                    ▼                ▼                   ▼
     │               [approved]       [rejected]     [action_required]
     │         (is_verified=True)                           │
     │                                                 resubmit│
     │                                                       ▼
     └───────────────────────────────────────────────── [submitted]

  Any status ─── expire ──► [expired]   (admin or system can expire at any point)
```

**Terminal states:** `approved`, `rejected`, `expired` — no further transitions allowed.

---

## `VALID_TRANSITIONS` (in `id_verification/models.py`)

```python
VALID_TRANSITIONS = {
    'draft':           ('submitted',),
    'submitted':       ('under_review', 'expired'),
    'under_review':    ('approved', 'rejected', 'action_required', 'expired'),
    'action_required': ('submitted', 'expired'),
}
```

Any `transition_to(new_status)` call with a `new_status` not in the current state's tuple raises
`ValidationError` immediately.

---

## Model: `IdentityVerification`

### Identity fields

| Field | Type | Required for submit |
|-------|------|---------------------|
| `document_type` | CharField | Yes — `national_id \| passport \| drivers_license \| residence_permit` |
| `document_number` | CharField | Yes — ID/passport/license number |
| `issuing_country` | CharField | Yes |
| `expiry_date` | DateField (null) | No — validated: cannot be in the past |
| `document_front` | ImageField | Yes — front of document |
| `document_back` | ImageField | No — back of document (optional) |
| `selfie` | ImageField | Yes — photo of instructor holding document |
| `resume` | FileField (PDF) | No |

`REQUIRED_FOR_SUBMIT = ('document_type', 'document_number', 'issuing_country', 'document_front', 'selfie')`

These fields must all be non-empty before `transition_to('submitted')` is allowed. Attempting to
submit with any missing raises `ValidationError` with the list of missing fields.

### Lifecycle state

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK → `User` (instructor) | |
| `status` | CharField | Current state |
| `submitted_at` | DateTimeField (null) | Set/updated on each submit or resubmit |
| `created_at`, `updated_at` | auto | Via `TimestampedModel` |

### Admin review fields

Only writable during review transitions. Returned to admins; hidden from instructor endpoints.

| Field | Type | Notes |
|-------|------|-------|
| `reviewed_by` | FK → `User` (admin) | Required for approve/reject/action_required transitions |
| `reviewed_at` | DateTimeField (null) | Set during admin review |
| `rejection_reason` | TextField | Required when transitioning to `rejected` |
| `action_required_reason` | TextField | Required when transitioning to `action_required` |
| `admin_notes` | TextField | Internal admin notes, never exposed to instructor |

---

## `transition_to()` — full logic

```python
def transition_to(self, new_status, reviewer=None,
                  rejection_reason='', action_required_reason='', admin_notes=''):

  1. Check VALID_TRANSITIONS[self.status]:
     → if new_status not in allowed: raise ValidationError("Cannot transition...")

  2. If transitioning to 'submitted':
     → _validate_completeness(): check all REQUIRED_FOR_SUBMIT fields are non-empty
     → set self.submitted_at = now()

  3. If new_status == 'rejected':
     → rejection_reason required: raise ValidationError if blank

  4. If new_status == 'action_required':
     → action_required_reason required: raise ValidationError if blank

  5. If new_status in ('under_review', 'approved', 'rejected', 'action_required', 'expired'):
     → reviewer required: raise ValidationError if reviewer is None

  6. Apply state:
     self.status = new_status
     self.reviewed_by = reviewer
     self.reviewed_at = now() (for admin transitions)
     self.rejection_reason = rejection_reason (if rejecting)
     self.action_required_reason = action_required_reason (if requesting action)
     self.admin_notes = admin_notes
     self.save()

  7. Side effect on 'approved':
     → _mark_instructor_verified()
     → InstructorProfile.objects.filter(user=self.user).update(is_verified=True)
```

---

## Instructor endpoints

All require `IsAuthenticated + IsEmailVerified + IsInstructorUser`.

```
POST   /api/v1/verification/create/          → create draft IdentityVerification
PATCH  /api/v1/verification/{id}/update/     → update document fields while in draft/action_required
POST   /api/v1/verification/{id}/submit/     → transition to 'submitted' (requires all fields)
GET    /api/v1/verification/my/              → list own verification records
GET    /api/v1/verification/my/{id}/         → detail of one verification record
```

**Create:** Creates a new `IdentityVerification` with `status='draft'`. An instructor can only have
one active (non-expired, non-rejected) verification at a time — the serializer or view enforces this.

**Update:** Allowed only when `status in ('draft', 'action_required')`. Updating a submitted/in-review
record is not permitted.

**Submit:** Calls `transition_to('submitted')`. The model validates completeness. Returns 400 with
field errors if any required fields are missing. Returns 422 if the transition itself is invalid
(e.g., already submitted).

---

## Admin endpoints

All require `IsAuthenticated + IsEmailVerified + IsPlatformAdmin`.

```
GET    /api/v1/verification/admin/list/           → paginated list (filter by ?status=)
GET    /api/v1/verification/admin/{id}/           → full detail with documents + review notes
POST   /api/v1/verification/admin/{id}/review/    → perform admin action
```

**Review action body:**

| `action` | Target status | Extra required fields |
|----------|--------------|----------------------|
| `pick_up` | `under_review` | — |
| `approve` | `approved` | — |
| `reject` | `rejected` | `rejection_reason` |
| `request_action` | `action_required` | `action_required_reason` |
| `expire` | `expired` | — |

The view maps `action` → `target_status`, then calls
`verification.transition_to(target_status, reviewer=request.user, ...)`.

---

## End-to-end workflow

```
Instructor                          Admin
─────────────────────────────────────────────────────────────
POST /verification/create/
  → status: draft
         │
PATCH /verification/{id}/update/
  → upload document_front, selfie, fill fields
         │
POST /verification/{id}/submit/
  → validates completeness
  → status: submitted
         │                               GET /verification/admin/list/?status=submitted
         │                               → sees pending submissions
         │
         │                               POST /verification/admin/{id}/review/
         │                               { "action": "pick_up" }
         │                               → status: under_review
         │
         │                               (admin reviews documents)
         │
         │                               POST /verification/admin/{id}/review/
         │                               { "action": "approve" }
         │                               → status: approved
         │                               → InstructorProfile.is_verified = True
         │
Now IsVerifiedInstructor passes
→ instructor can create/publish courses
```

**Rejection path:**
```
POST /verification/admin/{id}/review/
{ "action": "reject", "rejection_reason": "Document photo is blurry." }
→ status: rejected
→ instructor reads rejection_reason on GET /verification/my/{id}/

POST /verification/{id}/update/ → re-upload clear documents
POST /verification/{id}/submit/ → resubmit (status → submitted again)
```

**Action required path:**
```
POST /verification/admin/{id}/review/
{ "action": "request_action", "action_required_reason": "Please upload a selfie holding document." }
→ status: action_required

PATCH /verification/{id}/update/ → upload selfie
POST  /verification/{id}/submit/ → resubmit (action_required → submitted)
```

---

## Why this design

- **State-machine transitions** prevent invalid or skipped review steps — an admin cannot
  `approve` a draft directly, and an instructor cannot self-approve.
- **Required-field checks at submission** (not at creation) allow instructors to save drafts
  progressively without being forced to upload everything at once.
- **Approval side-effect is in the model** (`transition_to` calls `_mark_instructor_verified`)
  so the link between verification status and instructor permission is guaranteed regardless of
  which code path triggers the transition.
- **`admin_notes` never returned to instructors** — admins need a place to record internal
  discussion without the instructor seeing it, while `rejection_reason` and `action_required_reason`
  are the official communications.
