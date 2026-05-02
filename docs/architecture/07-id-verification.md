# 07) Identity Verification

Instructor identity verification is a state-machine workflow.

## Key files

- `id_verification/models.py`: `IdentityVerification` model and transition rules
- `id_verification/urls.py`: instructor and admin endpoints
- `id_verification/views.py`: export layer
- `id_verification/all_views/instructor_views.py`: draft/update/submit/my-list/my-detail
- `id_verification/all_views/admin_views.py`: admin list/detail/review actions
- `id_verification/serializers.py`: payload validation
- `id_verification/utils.py`: upload paths/helpers

## Model: `IdentityVerification`

- `user` (FK -> instructor user)
- Document info:
  - `document_type`, `document_number`, `issuing_country`, `expiry_date`
- Files:
  - `document_front`, `document_back`, `selfie`, `resume`
- Status:
  - `draft`, `submitted`, `under_review`, `action_required`, `approved`, `rejected`, `expired`
- Review fields:
  - `reviewed_by`, `reviewed_at`
  - `rejection_reason`
  - `action_required_reason`
  - `admin_notes`
- Timestamps:
  - `created_at`, `submitted_at`, `updated_at`

## State transition process

Allowed transitions are enforced in model logic (`transition_to`):

- `draft -> submitted`
- `submitted -> under_review|expired`
- `under_review -> approved|rejected|action_required|expired`
- `action_required -> submitted|expired`

Rules:

- Submission requires required document fields.
- Rejected/action-required require reasons.
- Review transitions require reviewer.
- On approval, linked `InstructorProfile.is_verified` is updated to `True`.

## API route groups

- Instructor:
  - `/verification/create/`
  - `/verification/{id}/update/`
  - `/verification/{id}/submit/`
  - `/verification/my/`
- Admin:
  - `/verification/admin/list/`
  - `/verification/admin/{id}/`
  - `/verification/admin/{id}/review/`

## Workflow

1. Instructor creates verification draft and uploads documents.
2. Instructor submits request when required fields are complete.
3. Admin reviews and transitions status using allowed state changes.
4. Approval triggers instructor verification flag update.

## System Explanation (Why This Design)

- State-machine transitions prevent invalid moderation actions.
- Required-field checks ensure reviewable submissions.
- Coupling approval to instructor verification keeps permissions consistent.
