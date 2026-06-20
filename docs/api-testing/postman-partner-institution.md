# Postman Guide — Partner Institution (Verification · Experts · Course Roster)

Manual API testing for the partner-institution Phase-1 feature set:

1. **Institution identity verification** — submit credentials, admin approves → `is_verified=True`.
2. **Expert management** — verified institution auto-provisions + manages experts (instructors).
3. **Course creation** — verified institution creates a course.
4. **Instructor assignment** — institution directly adds/removes its experts to a course roster.

---

## Environment Variables

Set these in your Postman environment before running the collection.

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `institution_token` | `Bearer eyJ...` | JWT for the partner institution admin (`user_type=partner_institution`) |
| `other_institution_token` | `Bearer eyJ...` | JWT for a second, unrelated partner institution |
| `admin_token` | `Bearer eyJ...` | JWT for a platform admin (`user_type=admin` / `is_staff`) |
| `expert_token` | `Bearer eyJ...` | JWT for a provisioned expert (after they verify email + set a password) |
| `learner_token` | `Bearer eyJ...` | JWT for any learner — used for negative authz tests |
| `verification_id` | _(filled during tests)_ | PK of an `InstitutionVerification` |
| `expert_id` | _(filled during tests)_ | PK of the expert's `InstructorProfile` |
| `expert_user_id` | _(filled during tests)_ | PK of the expert's `User` (used for roster add/remove) |
| `course_pk` | _(filled during tests)_ | PK of a course owned by the institution |

> **Account setup:** register a partner institution via `POST {{base_url}}/auth/register/` with
> `user_type=partner_institution`, `institution_name`, `institution_type`, then verify the OTP.
> The institution starts **unverified** (`is_verified=False`) — that is the precondition for Group 1.

---

## Access-Denied Policy (applies throughout)

| URL identifier | No-access response |
|---|---|
| Slug (none here) | 403 |
| Numeric ID (verification id, expert id, course pk) | **404** (existence not leaked) |
| Wrong `user_type` on a typed endpoint | 403 |
| Unverified institution on a verified-gated endpoint | 403 |

---

## Group 1: Institution Verification

> Precondition: `institution_token` is an **unverified** partner institution.

### 1.1 Create a draft verification

```
POST {{base_url}}/verification/institution/create/
Authorization: {{institution_token}}
Content-Type: multipart/form-data

registration_number: REG-2026-001
issuing_authority: Ministry of Education
official_email: registrar@acme.edu
accreditation_document: <file: accreditation.pdf>
authorization_letter: <file: authorization.pdf>   (optional)
```

**Expected:** `201 Created`, `data.status == "draft"`. Save `data.id` → `verification_id`.

**Postman Test:**
```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.test("status draft", () => pm.expect(pm.response.json().data.status).to.equal("draft"));
pm.environment.set("verification_id", pm.response.json().data.id);
```

---

### 1.2 Update the draft

```
PATCH {{base_url}}/verification/institution/{{verification_id}}/update/
Authorization: {{institution_token}}
Content-Type: application/json

{
    "registration_number": "REG-2026-002"
}
```

**Expected:** `200 OK`, `data.registration_number == "REG-2026-002"`.

---

### 1.3 Submit incomplete draft — 400

Create a second empty draft first (no documents), then submit it.

```
POST {{base_url}}/verification/institution/{{verification_id}}/submit/
Authorization: {{institution_token}}
```

**Expected:** `400 Bad Request`. `errors` lists the missing required fields
(`registration_number`, `issuing_authority`, `accreditation_document`).

---

### 1.4 Submit a complete draft — happy path

Using the draft from 1.1 (has all required fields):

```
POST {{base_url}}/verification/institution/{{verification_id}}/submit/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`, `data.status == "submitted"`. Platform admins receive an
`institution_verification.submitted` notification.

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("status submitted", () => pm.expect(pm.response.json().data.status).to.equal("submitted"));
```

---

### 1.5 List my verifications

```
GET {{base_url}}/verification/institution/my/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`, array containing the submitted request. `reviewed_by_email` is `null` until reviewed.

---

### 1.6 Detail of one verification

```
GET {{base_url}}/verification/institution/my/{{verification_id}}/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`. Note: `admin_notes` is **not** present in the institution-facing serializer.

---

### 1.7 Learner cannot access — 403

```
POST {{base_url}}/verification/institution/create/
Authorization: {{learner_token}}
```

**Expected:** `403 Forbidden`, `message: "Only partner institutions can access this resource."`.

---

## Group 2: Admin Review of Institution Verification

> Use `admin_token` for all of Group 2.

### 2.1 List submitted verifications

```
GET {{base_url}}/verification/admin/institution/list/?status=submitted
Authorization: {{admin_token}}
```

**Expected:** `200 OK`, paginated, includes the request from 1.4.

---

### 2.2 Detail (admin view, includes admin_notes)

```
GET {{base_url}}/verification/admin/institution/{{verification_id}}/
Authorization: {{admin_token}}
```

**Expected:** `200 OK`. Includes `admin_notes`, `institution_name`, `institution_slug`.

---

### 2.3 Pick up for review

```
POST {{base_url}}/verification/admin/institution/{{verification_id}}/review/
Authorization: {{admin_token}}
Content-Type: application/json

{ "action": "pick_up" }
```

**Expected:** `200 OK`, `data.status == "under_review"`.

---

### 2.4 Request action (needs reason)

```
POST {{base_url}}/verification/admin/institution/{{verification_id}}/review/
Authorization: {{admin_token}}
Content-Type: application/json

{ "action": "request_action", "action_required_reason": "Accreditation document is illegible." }
```

**Expected:** `200 OK`, `data.status == "action_required"`. Institution receives
`institution_verification.action_required`. (Institution can now update + re-submit.)

> Missing `action_required_reason` → `400`.

---

### 2.5 Reject without reason — 400

```
POST {{base_url}}/verification/admin/institution/{{verification_id}}/review/
Authorization: {{admin_token}}
Content-Type: application/json

{ "action": "reject" }
```

**Expected:** `400 Bad Request`, `errors.rejection_reason` present.

---

### 2.6 Approve — flips institution to verified

The request must be in `under_review` (re-submit + pick up again if you ran 2.4).

```
POST {{base_url}}/verification/admin/institution/{{verification_id}}/review/
Authorization: {{admin_token}}
Content-Type: application/json

{ "action": "approve" }
```

**Expected:** `200 OK`, `data.status == "approved"`. Side effect:
`PartnerInstitutionProfile.is_verified = True` (+ `is_active = True`). Institution receives
`institution_verification.approved`.

**Postman Test:**
```javascript
pm.test("approved", () => pm.expect(pm.response.json().data.status).to.equal("approved"));
```

> **Re-issue your `institution_token` is not needed** — the JWT is unchanged; only the profile flag flipped.
> Verify via the public profile: `GET {{base_url}}/auth/profiles/<slug>/` → `is_verified: true`.

---

### 2.7 `expire` action invalid for institutions — 422

```
POST {{base_url}}/verification/admin/institution/{{verification_id}}/review/
Authorization: {{admin_token}}
Content-Type: application/json

{ "action": "expire" }
```

**Expected:** `422 Unprocessable Entity`. (Institution verification has no `expired` state — unlike instructor ID verification.)

---

## Group 3: Expert Management

> Precondition: institution is **verified** (Group 2.6 done). All endpoints require
> `IsVerifiedPartnerInstitution`.

### 3.1 Onboard an expert — happy path

```
POST {{base_url}}/auth/partner/experts/
Authorization: {{institution_token}}
Content-Type: application/json

{
    "full_name": "Jane Expert",
    "email": "jane.expert@example.com",
    "bio": "10 years in applied ML.",
    "headline": "Senior ML Engineer",
    "specialization": ["NLP", "Computer Vision"]
}
```

**Expected:** `201 Created`, `message: "Expert onboarded. An activation email has been sent."`.
The system creates a `User(user_type=instructor)` + `InstructorProfile` with
`affiliation_status=active`, `onboarding_source=institution`, `is_verified=true`, and sends an
activation OTP email (printed to the console with the dev email backend).

```json
{
    "success": true,
    "message": "Expert onboarded. An activation email has been sent.",
    "data": {
        "id": 12,
        "full_name": "Jane Expert",
        "email": "jane.expert@example.com",
        "slug": "jane-expert",
        "headline": "Senior ML Engineer",
        "bio": "10 years in applied ML.",
        "specialization": ["NLP", "Computer Vision"],
        "is_verified": true,
        "is_email_verified": false,
        "affiliation_status": "active",
        "onboarding_source": "institution",
        "affiliated_at": "2026-06-20T10:00:00Z",
        "course_count": 0
    }
}
```

**Postman Test:**
```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.environment.set("expert_id", pm.response.json().data.id);
// expert_user_id is the User PK — fetch it from Django admin or the list endpoint if needed.
```

> **Expert activation:** the expert verifies the OTP (`POST /auth/otp/verify/`) then sets a password
> via the forgot/reset flow (`POST /auth/password/forgot/` → `POST /auth/password/reset/`). After that
> they can log in and obtain `expert_token`.

---

### 3.2 Duplicate email blocked — 422

```
POST {{base_url}}/auth/partner/experts/
Authorization: {{institution_token}}
Content-Type: application/json

{ "full_name": "Jane Again", "email": "jane.expert@example.com" }
```

**Expected:** `422 Unprocessable Entity`, `message: "A user with this email already exists."`.

---

### 3.3 List experts

```
GET {{base_url}}/auth/partner/experts/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`, paginated. Each row carries `course_count` (annotated — no N+1).

---

### 3.4 Expert detail

```
GET {{base_url}}/auth/partner/experts/{{expert_id}}/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`, the expert's profile.

---

### 3.5 Edit expert profile

```
PATCH {{base_url}}/auth/partner/experts/{{expert_id}}/
Authorization: {{institution_token}}
Content-Type: application/json

{ "headline": "Principal ML Engineer", "specialization": ["NLP"] }
```

**Expected:** `200 OK`, fields updated.

---

### 3.6 Deactivate an expert

```
PATCH {{base_url}}/auth/partner/experts/{{expert_id}}/
Authorization: {{institution_token}}
Content-Type: application/json

{ "is_active": false }
```

**Expected:** `200 OK`, `data.affiliation_status == "removed"`, `data.is_verified == false`.
A removed expert can no longer author and cannot be assigned to courses.

> Re-activate with `{ "is_active": true }` → `affiliation_status` back to `active`, `is_verified` true.

---

### 3.7 Unverified institution blocked — 403

Use a partner institution that has NOT been verified.

```
GET {{base_url}}/auth/partner/experts/
Authorization: {{institution_token}}   (unverified institution)
```

**Expected:** `403 Forbidden`.

---

### 3.8 Cannot see another institution's expert — 404

```
GET {{base_url}}/auth/partner/experts/{{expert_id}}/
Authorization: {{other_institution_token}}
```

**Expected:** `404 Not Found` (numeric ID — existence not leaked).

---

## Group 4: Course Creation (Partner Institution)

> Precondition: institution verified.

### 4.1 Create a course — happy path

```
POST {{base_url}}/courses/create/
Authorization: {{institution_token}}
Content-Type: application/json

{
    "title": "Self-Paced Data Engineering",
    "description": "An asynchronous institutional training course."
}
```

**Expected:** `201 Created`, `data.status == "draft"`. The course's `created_by` is the institution
user and `partner_institution` is auto-set. Save `data.id` → `course_pk`.

**Postman Test:**
```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.environment.set("course_pk", pm.response.json().data.id);
```

---

### 4.2 Unverified institution cannot create — 403

```
POST {{base_url}}/courses/create/
Authorization: {{institution_token}}   (unverified institution)
Content-Type: application/json

{ "title": "Blocked Course", "description": "Should be rejected." }
```

**Expected:** `403 Forbidden` (`IsVerifiedCourseCreator`).

---

## Group 5: Instructor (Expert) Assignment to a Course

> Precondition: `course_pk` owned by the institution; `expert_user_id` is an **active**
> affiliated expert's **User** PK.

### 5.1 Assign expert — happy path

```
POST {{base_url}}/courses/{{course_pk}}/institution-instructors/
Authorization: {{institution_token}}
Content-Type: application/json

{ "expert_user_id": {{expert_user_id}} }
```

**Expected:** `200 OK`, `message: "Expert assigned to course."`. Verify via course detail:

```
GET {{base_url}}/courses/{{course_pk}}/
Authorization: {{institution_token}}
```
`data.instructors` now contains the expert.

---

### 5.2 Assigned expert can edit course content

After 5.1, log in as the expert (`expert_token`) and edit the course:

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{expert_token}}
Content-Type: application/json

{ "description": "Edited by the assigned expert." }
```

**Expected:** `200 OK`. The expert (now in `instructors`) passes the authoring guard.

---

### 5.3 Missing `expert_user_id` — 400

```
POST {{base_url}}/courses/{{course_pk}}/institution-instructors/
Authorization: {{institution_token}}
Content-Type: application/json

{ }
```

**Expected:** `400 Bad Request`, `errors.expert_user_id` present.

---

### 5.4 Non-numeric `expert_user_id` — 400

```
POST {{base_url}}/courses/{{course_pk}}/institution-instructors/
Authorization: {{institution_token}}
Content-Type: application/json

{ "expert_user_id": "abc" }
```

**Expected:** `400 Bad Request`, `errors.expert_user_id: "Must be a valid user id."`.

---

### 5.5 Assign a non-affiliated / inactive user — 422

Use a `User` PK that is not an active affiliated expert (e.g. an outside instructor, or a
deactivated expert from 3.6).

```
POST {{base_url}}/courses/{{course_pk}}/institution-instructors/
Authorization: {{institution_token}}
Content-Type: application/json

{ "expert_user_id": <outsider_user_id> }
```

**Expected:** `422 Unprocessable Entity`,
`message: "This user is not an active expert of your institution."`.

---

### 5.6 Assign an already-assigned expert — 422

Re-run 5.1 with the same expert.

**Expected:** `422 Unprocessable Entity`,
`message: "This expert is already an instructor on this course."`.

---

### 5.7 Foreign course — 404

```
POST {{base_url}}/courses/{{course_pk}}/institution-instructors/
Authorization: {{other_institution_token}}
Content-Type: application/json

{ "expert_user_id": {{expert_user_id}} }
```

**Expected:** `404 Not Found` (course not owned by caller — existence not leaked).

---

### 5.8 Remove expert from roster

```
DELETE {{base_url}}/courses/{{course_pk}}/institution-instructors/{{expert_user_id}}/
Authorization: {{institution_token}}
```

**Expected:** `200 OK`, `message: "Expert removed from course."`. Expert no longer in
`data.instructors`; their edit access is revoked.

> Removing a user not on the roster → `422` (`"This expert is not an instructor on this course."`).

---

### 5.9 Roster change on a locked course — 422

Submit the course for review (`POST {{base_url}}/courses/{{course_pk}}/submit/`) so it leaves
`draft`, then attempt 5.1 again.

**Expected:** `422 Unprocessable Entity`,
`message: "This course is locked and its roster cannot be changed."`.

---

## Response Shape Reference

**Success (action, no body):**
```json
{ "success": true, "message": "Expert assigned to course." }
```

**Verification detail (institution-facing):**
```json
{
    "success": true,
    "data": {
        "id": 1,
        "registration_number": "REG-2026-002",
        "issuing_authority": "Ministry of Education",
        "official_email": "registrar@acme.edu",
        "accreditation_document": "/media/institution_verification/accreditation/...pdf",
        "authorization_letter": null,
        "status": "approved",
        "rejection_reason": "",
        "action_required_reason": "",
        "reviewed_by_email": "admin@example.com",
        "reviewed_at": "2026-06-20T10:05:00Z",
        "created_at": "...",
        "submitted_at": "...",
        "updated_at": "..."
    }
}
```

**Error (4xx):**
```json
{ "success": false, "message": "This user is not an active expert of your institution." }
```

**Validation error (400 with field errors):**
```json
{ "success": false, "message": "Validation failed.", "errors": { "expert_user_id": "Must be a valid user id." } }
```

---

## Error Code Summary

| Scenario | Status |
|----------|--------|
| Learner / wrong user_type on institution-verification endpoint | 403 |
| Submit incomplete verification | 400 |
| Reject without reason / request_action without reason | 400 |
| `expire` action on institution verification | 422 |
| Unverified institution on expert/course endpoint | 403 |
| Duplicate expert email | 422 |
| Another institution's expert (numeric ID) | 404 |
| Course created by unverified institution | 403 |
| Missing / non-numeric `expert_user_id` | 400 |
| Assign non-affiliated or inactive expert | 422 |
| Assign already-assigned expert | 422 |
| Foreign course (numeric ID) | 404 |
| Roster change on locked course | 422 |

---

## Recommended Run Order

```
─ verification ─
1.1  Create draft                 → save verification_id
1.4  Submit (complete)            → status submitted
2.3  Admin pick_up                → under_review
2.6  Admin approve                → institution is_verified=true

─ experts ─
3.1  Onboard expert               → save expert_id (+ expert_user_id)
3.3  List experts                 → course_count present
3.6  Deactivate / reactivate

─ course + roster ─
4.1  Create course (verified)     → save course_pk
5.1  Assign expert                → expert in instructors
5.2  Expert edits course          → 200
5.8  Remove expert                → roster empty

─ error paths ─
1.7  Learner → 403
2.5  Reject w/o reason → 400
2.7  expire action → 422
3.2  Duplicate email → 422
3.8  Other institution's expert → 404
4.2  Unverified create → 403
5.4  Non-numeric id → 400
5.5  Non-affiliated → 422
5.7  Foreign course → 404
5.9  Locked course → 422
```
