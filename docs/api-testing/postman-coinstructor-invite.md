# Postman Guide — Co-Instructor Invitation Flow

Manual API testing for the full co-instructor invitation pipeline: send, list, revoke, accept, decline, and expiry.

---

## Environment Variables

Set these in your Postman environment before running the collection.

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `owner_token` | `Bearer eyJ...` | JWT for the course owner (`created_by`) |
| `invitee_token` | `Bearer eyJ...` | JWT for a verified instructor who will be invited |
| `coinstructor_token` | `Bearer eyJ...` | JWT for an instructor already on the course (not owner) |
| `unrelated_token` | `Bearer eyJ...` | JWT for a verified instructor with no connection to the course |
| `course_pk` | `1` | PK of a draft course owned by owner |
| `owner_email` | `owner@example.com` | Email of the owner |
| `invitee_email` | `invitee@example.com` | Email of the invitee (verified instructor) |
| `invite_id` | _(filled during tests)_ | PK of a created `CourseInstructorInvite` |
| `invite_token` | _(filled during tests)_ | UUID token of a pending invite (from invitee's `my` endpoint) |

---

## Setup Sequence

Run these once to create the fixtures.

### Step 1 — Create a course (as owner)

```
POST {{base_url}}/courses/create/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "title": "Invite Flow Test Course",
    "description": "Testing co-instructor invitations."
}
```

**Expected:** `201 Created`, `data.status == "draft"`. Save `data.id` → `course_pk`.

### Step 2 — Confirm invitee is not yet an instructor

```
GET {{base_url}}/courses/{{course_pk}}/
Authorization: {{owner_token}}
```

**Expected:** `200 OK`. `data.instructors` contains only the owner — invitee is absent.

---

## Group 1: Send Invite

### 1.1 Owner sends invite — happy path

```
POST {{base_url}}/courses/{{course_pk}}/instructors/invite/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "email": "{{invitee_email}}"
}
```

**Expected:** `201 Created`.

```json
{
    "success": true,
    "message": "Invite sent successfully.",
    "data": {
        "id": 1,
        "course": 1,
        "course_title": "Invite Flow Test Course",
        "invited_by": 2,
        "invited_by_name": "Alice Owner",
        "invited_user": 5,
        "invited_user_name": "Bob Invitee",
        "invited_user_email": "invitee@example.com",
        "status": "pending",
        "expires_at": "2026-06-16T10:00:00Z",
        "responded_at": null,
        "created_at": "2026-06-09T10:00:00Z",
        "updated_at": "2026-06-09T10:00:00Z"
    }
}
```

Save `data.id` → `invite_id`.

> **Note:** `token` does NOT appear in this response. It is sent exclusively in the email.

**Postman Test:**
```javascript
pm.test("201 created", () => pm.response.to.have.status(201));
pm.test("status is pending", () => {
    pm.expect(pm.response.json().data.status).to.equal("pending");
});
pm.test("token not exposed to owner", () => {
    pm.expect(pm.response.json().data).to.not.have.property("token");
});
pm.environment.set("invite_id", pm.response.json().data.id);
```

---

### 1.2 Co-instructor cannot send invite — 404

```
POST {{base_url}}/courses/{{course_pk}}/instructors/invite/
Authorization: {{coinstructor_token}}
Content-Type: application/json

{
    "email": "{{invitee_email}}"
}
```

**Expected:** `404 Not Found`. (Numeric-ID endpoint — non-owners get 404, not 403, per policy.)

**Postman Test:**
```javascript
pm.test("co-instructor gets 404 not 403", () => pm.response.to.have.status(404));
```

---

### 1.3 Owner cannot invite themselves — 400

```
POST {{base_url}}/courses/{{course_pk}}/instructors/invite/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "email": "{{owner_email}}"
}
```

**Expected:** `400 Bad Request`, `message: "You cannot invite yourself."`.

---

### 1.4 Duplicate pending invite blocked — 400

Send the same invite a second time (invitee is still pending from 1.1).

```
POST {{base_url}}/courses/{{course_pk}}/instructors/invite/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "email": "{{invitee_email}}"
}
```

**Expected:** `400 Bad Request`, `message: "A pending invite already exists for this user."`.

---

### 1.5 Non-instructor email blocked — 400

Use the email of a learner account (or any user who is not `user_type=instructor`).

```
POST {{base_url}}/courses/{{course_pk}}/instructors/invite/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "email": "learner@example.com"
}
```

**Expected:** `400 Bad Request`, `message: "No verified instructor found with this email."`.

---

### 1.6 Already-instructor email blocked — 400

First accept the invite from Group 5 (or manually add to M2M in admin), then try to invite again.

```
POST {{base_url}}/courses/{{course_pk}}/instructors/invite/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "email": "{{invitee_email}}"
}
```

**Expected:** `400 Bad Request`, `message: "This user is already an instructor on this course."`.

---

### 1.7 Non-editable course blocks invite — 422

Submit the course for review first (`POST .../submit/`), then try to send an invite.

```
POST {{base_url}}/courses/{{course_pk}}/instructors/invite/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "email": "{{invitee_email}}"
}
```

**Expected:** `422 Unprocessable Entity`. Message contains "cannot be edited".

> Restore the course to draft (`POST .../rework/` after admin rejection, or set via admin panel) before continuing.

---

## Group 2: List Invites for a Course

### 2.1 Owner lists invites — success

```
GET {{base_url}}/courses/{{course_pk}}/instructors/invites/
Authorization: {{owner_token}}
```

**Expected:** `200 OK`, paginated list containing the pending invite from 1.1.

```json
{
    "success": true,
    "data": {
        "count": 1,
        "next": null,
        "previous": null,
        "results": [
            {
                "id": 1,
                "status": "pending",
                "invited_user_email": "invitee@example.com",
                ...
            }
        ]
    }
}
```

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("token not in results", () => {
    const results = pm.response.json().data.results;
    results.forEach(r => pm.expect(r).to.not.have.property("token"));
});
```

---

### 2.2 Co-instructor cannot list invites — 404

```
GET {{base_url}}/courses/{{course_pk}}/instructors/invites/
Authorization: {{coinstructor_token}}
```

**Expected:** `404 Not Found`.

---

### 2.3 Filter by status — pending

```
GET {{base_url}}/courses/{{course_pk}}/instructors/invites/?status=pending
Authorization: {{owner_token}}
```

**Expected:** `200 OK`, all results have `status == "pending"`.

---

### 2.4 Filter by status — accepted (empty after setup)

```
GET {{base_url}}/courses/{{course_pk}}/instructors/invites/?status=accepted
Authorization: {{owner_token}}
```

**Expected:** `200 OK`, `data.count == 0`, `data.results == []`.

---

### 2.5 Invalid status filter — 400

```
GET {{base_url}}/courses/{{course_pk}}/instructors/invites/?status=bogus
Authorization: {{owner_token}}
```

**Expected:** `400 Bad Request`. Message contains valid choices.

---

## Group 3: Revoke an Invite

### 3.1 Owner revokes pending invite — success

```
DELETE {{base_url}}/courses/{{course_pk}}/instructors/invites/{{invite_id}}/
Authorization: {{owner_token}}
```

**Expected:** `200 OK`.

```json
{
    "success": true,
    "message": "Invite revoked."
}
```

Verify via list:
```
GET {{base_url}}/courses/{{course_pk}}/instructors/invites/?status=revoked
Authorization: {{owner_token}}
```
`data.count == 1`.

> After this test, re-send the invite (repeat 1.1) to restore a pending invite for Groups 4–6.

---

### 3.2 Co-instructor cannot revoke — 404

```
DELETE {{base_url}}/courses/{{course_pk}}/instructors/invites/{{invite_id}}/
Authorization: {{coinstructor_token}}
```

**Expected:** `404 Not Found`.

---

### 3.3 Non-pending invite cannot be revoked — 422

Set the invite to `accepted` via admin panel, or run Group 5 first. Then:

```
DELETE {{base_url}}/courses/{{course_pk}}/instructors/invites/{{invite_id}}/
Authorization: {{owner_token}}
```

**Expected:** `422 Unprocessable Entity`, `message: "Only pending invites can be revoked."`.

---

### 3.4 Unknown invite ID — 404

```
DELETE {{base_url}}/courses/{{course_pk}}/instructors/invites/99999/
Authorization: {{owner_token}}
```

**Expected:** `404 Not Found`.

---

## Group 4: My Received Invites (Invitee)

> Precondition: a pending invite exists for the invitee (from 1.1).

### 4.1 Invitee lists pending invites

```
GET {{base_url}}/courses/invites/my/
Authorization: {{invitee_token}}
```

**Expected:** `200 OK`, paginated, `results[0].status == "pending"`.

**Postman Test — save token for accept/decline:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("token present for invitee", () => {
    const result = pm.response.json().data.results[0];
    pm.expect(result).to.have.property("token");
    pm.environment.set("invite_token", result.token);
});
```

---

### 4.2 Filter by status — explicit pending

```
GET {{base_url}}/courses/invites/my/?status=pending
Authorization: {{invitee_token}}
```

**Expected:** Same as 4.1.

---

### 4.3 Filter by status — accepted (empty before accepting)

```
GET {{base_url}}/courses/invites/my/?status=accepted
Authorization: {{invitee_token}}
```

**Expected:** `200 OK`, `data.results == []`.

---

### 4.4 Unrelated instructor sees no invites

```
GET {{base_url}}/courses/invites/my/
Authorization: {{unrelated_token}}
```

**Expected:** `200 OK`, `data.count == 0`.

---

### 4.5 Invalid status filter — 400

```
GET {{base_url}}/courses/invites/my/?status=invalid
Authorization: {{invitee_token}}
```

**Expected:** `400 Bad Request`.

---

## Group 5: Accept an Invite

> Precondition: `invite_token` is set from Group 4.1.

### 5.1 Invitee accepts — happy path

```
POST {{base_url}}/courses/invites/{{invite_token}}/accept/
Authorization: {{invitee_token}}
```

**Expected:** `200 OK`.

```json
{
    "success": true,
    "message": "You have joined \"Invite Flow Test Course\" as a co-instructor.",
    "data": {
        "status": "accepted",
        "token": "550e8400-...",
        ...
    }
}
```

**Postman Test:**
```javascript
pm.test("200 ok", () => pm.response.to.have.status(200));
pm.test("status accepted", () => {
    pm.expect(pm.response.json().data.status).to.equal("accepted");
});
pm.test("token present in invitee response", () => {
    pm.expect(pm.response.json().data).to.have.property("token");
});
```

Verify invitee now appears in the course roster:
```
GET {{base_url}}/courses/{{course_pk}}/
Authorization: {{owner_token}}
```
`data.instructors` now contains the invitee.

---

### 5.2 Wrong user cannot accept — 404

```
POST {{base_url}}/courses/invites/{{invite_token}}/accept/
Authorization: {{unrelated_token}}
```

**Expected:** `404 Not Found`, `message: "Invite not found."`.

---

### 5.3 Unknown token — 404

```
POST {{base_url}}/courses/invites/00000000-0000-0000-0000-000000000000/accept/
Authorization: {{invitee_token}}
```

**Expected:** `404 Not Found`.

---

### 5.4 Re-accepting an already-accepted invite — 410

Run 5.1 first, then call accept again with the same token.

```
POST {{base_url}}/courses/invites/{{invite_token}}/accept/
Authorization: {{invitee_token}}
```

**Expected:** `410 Gone`, `message: "This invite is no longer valid."`.

---

### 5.5 Accepting a revoked invite — 410

Create a new invite (1.1), revoke it (3.1), then try to accept.

```
POST {{base_url}}/courses/invites/{{invite_token}}/accept/
Authorization: {{invitee_token}}
```

**Expected:** `410 Gone`, `message: "This invite is no longer valid."`.

---

### 5.6 Accepting an expired invite — 410

Set `expires_at` to the past via Django admin (or wait 7 days), then accept.

```
POST {{base_url}}/courses/invites/{{invite_token}}/accept/
Authorization: {{invitee_token}}
```

**Expected:** `410 Gone`, `message: "This invite has expired."`.

> The invite's DB `status` will be swept to `expired` by the next run of `expire_instructor_invites_task`. The 410 response is immediate and does not depend on the sweep having run.

---

### 5.7 Accepting invite on a published course — 422

Submit and approve the course to `published` status. Create a fresh invite. Then accept.

```
POST {{base_url}}/courses/invites/{{invite_token}}/accept/
Authorization: {{invitee_token}}
```

**Expected:** `422 Unprocessable Entity`, `message: "This course is no longer accepting new instructors."`. Invitee NOT added to `data.instructors`.

---

## Group 6: Decline an Invite

> Precondition: create a fresh pending invite (repeat 1.1 after removing the old one).

### 6.1 Invitee declines — happy path

Retrieve the new token via 4.1, then:

```
POST {{base_url}}/courses/invites/{{invite_token}}/decline/
Authorization: {{invitee_token}}
```

**Expected:** `200 OK`.

```json
{
    "success": true,
    "message": "You have declined the invitation to \"Invite Flow Test Course\".",
    "data": {
        "status": "declined",
        ...
    }
}
```

Invitee NOT in `data.instructors` on course detail.

---

### 6.2 Wrong user cannot decline — 404

```
POST {{base_url}}/courses/invites/{{invite_token}}/decline/
Authorization: {{unrelated_token}}
```

**Expected:** `404 Not Found`.

---

### 6.3 Re-declining an already-declined invite — 410

```
POST {{base_url}}/courses/invites/{{invite_token}}/decline/
Authorization: {{invitee_token}}
```

**Expected:** `410 Gone`, `message: "This invite is no longer valid."`.

---

### 6.4 Declining a revoked invite — 410

```
POST {{base_url}}/courses/invites/{{invite_token}}/decline/
Authorization: {{invitee_token}}
```

**Expected:** `410 Gone`.

---

### 6.5 Re-invite after decline — allowed

After 6.1, owner can send a fresh invite to the same user.

```
POST {{base_url}}/courses/{{course_pk}}/instructors/invite/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "email": "{{invitee_email}}"
}
```

**Expected:** `201 Created`. A new pending invite is created.

---

## Group 7: Token Visibility Policy

These tests verify that the invite token (the accept/decline secret) is only exposed to the invitee, never to the owner.

### 7.1 Token absent from owner create response (1.1)

Already covered by the test in 1.1. Confirm `data` has no `token` key.

### 7.2 Token absent from owner list response (2.1)

Already covered by the test in 2.1. Confirm each result row has no `token` key.

### 7.3 Token present in invitee my-invites response (4.1)

Already covered by the test in 4.1.

### 7.4 Token present in invitee accept response (5.1)

Already covered by the test in 5.1.

---

## Response Shape Reference

**Send invite (201):**
```json
{
    "success": true,
    "message": "Invite sent successfully.",
    "data": {
        "id": 1,
        "course": 1,
        "course_title": "...",
        "invited_by": 2,
        "invited_by_name": "Alice Owner",
        "invited_user": 5,
        "invited_user_name": "Bob Invitee",
        "invited_user_email": "invitee@example.com",
        "status": "pending",
        "expires_at": "2026-06-16T10:00:00Z",
        "responded_at": null,
        "created_at": "...",
        "updated_at": "..."
    }
}
```

> Owner-facing responses (`send`, `list`) do **not** include `token`.  
> Invitee-facing responses (`my`, `accept`, `decline`) **do** include `token`.

**Error (4xx):**
```json
{
    "success": false,
    "message": "A pending invite already exists for this user."
}
```

**Validation error (400 with field errors):**
```json
{
    "success": false,
    "message": "Validation failed.",
    "errors": { "email": ["Enter a valid email address."] }
}
```

---

## Error Code Summary

| Scenario | Status |
|----------|--------|
| Course not found / caller not owner | 404 |
| Co-instructor calls owner endpoint | 404 |
| Course not editable (send) | 422 |
| Course not editable (accept) | 422 |
| Self-invite | 400 |
| Invitee already an instructor | 400 |
| Duplicate pending invite | 400 |
| Email not a verified instructor | 400 |
| Token not found / wrong user | 404 |
| Invite expired | 410 |
| Invite not pending (revoked / accepted / declined) | 410 |
| Revoke non-pending invite | 422 |
| Invalid `?status=` filter | 400 |

---

## Recommended Run Order

```
1.1  Send invite (happy path)       → save invite_id
2.1  List invites (owner)           → confirm pending
4.1  My invites (invitee)           → save invite_token
5.1  Accept invite                  → invitee joins course
5.4  Re-accept → 410
2.4  List accepted → count 1

─ reset: new invite ─

4.1  My invites → save new invite_token
6.1  Decline invite
6.3  Re-decline → 410
6.5  Re-invite after decline → 201

─ error paths ─

1.2  Co-instructor send → 404
1.3  Self-invite → 400
1.4  Duplicate → 400
1.5  Non-instructor email → 400
2.2  Co-instructor list → 404
3.2  Co-instructor revoke → 404
3.3  Revoke non-pending → 422
5.2  Wrong user accept → 404
5.7  Accept on published course → 422
```
