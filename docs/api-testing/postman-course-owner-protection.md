# Postman Guide — Course Owner Protection

This guide covers manual API testing for the multi-instructor collaboration and owner-protection feature. Merge with the main Postman guide when ready.

---

## Environment Variables

Set these in your Postman environment before running the collection.

| Variable | Example value | Notes |
|----------|--------------|-------|
| `base_url` | `http://localhost:8000/api/v1` | No trailing slash |
| `owner_token` | `Bearer eyJ...` | JWT for the course owner (created_by) |
| `coinstructor_token` | `Bearer eyJ...` | JWT for a verified co-instructor added to the course |
| `course_pk` | `1` | PK of a course in draft/rejected status |
| `owner_id` | `5` | User PK of the course owner |
| `coinstructor_id` | `12` | User PK of the co-instructor |
| `third_instructor_id` | `23` | User PK of a third verified instructor NOT yet on the course |

---

## Setup Sequence

Run these once before the main test collection to create the fixtures.

### Step 1 — Create a course (as owner)

```
POST {{base_url}}/courses/create/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "title": "Owner Protection Test Course",
    "description": "Testing multi-instructor owner protection."
}
```

**Expected:** `201 Created`, `data.status == "draft"`. Copy `data.id` → `course_pk`.

### Step 2 — Add co-instructor to the course (as owner)

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "instructors": [{{owner_id}}, {{coinstructor_id}}]
}
```

**Expected:** `200 OK`, `data.instructors` contains both users.

---

## Test Collection

### Group 1: Owner Operations (all should succeed)

#### 1.1 Owner can update title

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "title": "Owner Updated Title"
}
```

**Expected:** `200 OK`, `data.title == "Owner Updated Title"`.

#### 1.2 Owner can add third instructor

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "instructors": [{{owner_id}}, {{coinstructor_id}}, {{third_instructor_id}}]
}
```

**Expected:** `200 OK`, `data.instructors` has 3 members including `third_instructor_id`.

**Postman Test:**
```javascript
pm.test("third instructor added", function () {
    const ids = pm.response.json().data.instructors.map(i => i.id);
    pm.expect(ids).to.include(pm.environment.get("third_instructor_id") * 1);
});
```

#### 1.3 Owner omits self from patch — owner still re-added

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "instructors": [{{coinstructor_id}}]
}
```

**Expected:** `200 OK`, `data.instructors` still contains `owner_id`.

**Postman Test:**
```javascript
pm.test("owner still in roster", function () {
    const ids = pm.response.json().data.instructors.map(i => i.id);
    pm.expect(ids).to.include(pm.environment.get("owner_id") * 1);
});
```

---

### Group 2: Co-instructor Operations (roster/institution fields silently ignored)

#### 2.1 Co-instructor can update title

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{coinstructor_token}}
Content-Type: application/json

{
    "title": "Co-instructor Title Edit"
}
```

**Expected:** `200 OK`, `data.title == "Co-instructor Title Edit"`.

#### 2.2 Co-instructor cannot remove owner — roster unchanged

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{coinstructor_token}}
Content-Type: application/json

{
    "instructors": [{{coinstructor_id}}]
}
```

**Expected:** `200 OK` (not 403). Owner still in roster.

**Postman Tests:**
```javascript
pm.test("status 200", function () {
    pm.response.to.have.status(200);
});
pm.test("owner still in roster after co-instructor patch", function () {
    const ids = pm.response.json().data.instructors.map(i => i.id);
    pm.expect(ids).to.include(pm.environment.get("owner_id") * 1);
});
```

#### 2.3 Co-instructor cannot add third instructor — roster unchanged

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{coinstructor_token}}
Content-Type: application/json

{
    "instructors": [{{owner_id}}, {{coinstructor_id}}, {{third_instructor_id}}]
}
```

**Expected:** `200 OK`. `data.instructors` does NOT contain `third_instructor_id`.

**Postman Test:**
```javascript
pm.test("third instructor not added by co-instructor", function () {
    const ids = pm.response.json().data.instructors.map(i => i.id);
    pm.expect(ids).to.not.include(pm.environment.get("third_instructor_id") * 1);
});
```

#### 2.4 Co-instructor title + instructors in same payload — title updated, roster unchanged

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{coinstructor_token}}
Content-Type: application/json

{
    "title": "Mixed Payload Title",
    "instructors": [{{coinstructor_id}}]
}
```

**Expected:** `200 OK`, `data.title == "Mixed Payload Title"`, owner still in roster.

**Postman Tests:**
```javascript
pm.test("title updated", function () {
    pm.expect(pm.response.json().data.title).to.equal("Mixed Payload Title");
});
pm.test("owner still in roster", function () {
    const ids = pm.response.json().data.instructors.map(i => i.id);
    pm.expect(ids).to.include(pm.environment.get("owner_id") * 1);
});
```

---

### Group 3: Edge Cases

#### 3.1 Unauthenticated request returns 401

```
PATCH {{base_url}}/courses/{{course_pk}}/
Content-Type: application/json

{
    "title": "No auth"
}
```

**Expected:** `401 Unauthorized`.

#### 3.2 Verified instructor NOT on course returns 404

Use a token for a verified instructor who is not in `course.instructors`.

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{third_instructor_token}}
Content-Type: application/json

{
    "title": "Should not work"
}
```

**Expected:** `404 Not Found`. (ID-based endpoint — 404, not 403, to avoid existence leakage.)

#### 3.3 created_by field in PATCH body is ignored

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "created_by": {{coinstructor_id}}
}
```

**Expected:** `200 OK`. `data.created_by.id` still equals `owner_id`.

**Postman Test:**
```javascript
pm.test("created_by immutable", function () {
    pm.expect(pm.response.json().data.created_by.id).to.equal(pm.environment.get("owner_id") * 1);
});
```

#### 3.4 Non-draft course blocks all edits (edit lock)

Set the course to `under_review` status (via submit endpoint), then:

```
PATCH {{base_url}}/courses/{{course_pk}}/
Authorization: {{owner_token}}
Content-Type: application/json

{
    "title": "Locked"
}
```

**Expected:** `422 Unprocessable Entity`, `success: false`.

---

## Response Shape Reference

All endpoints follow this envelope:

**Success:**
```json
{
    "success": true,
    "message": "Course updated successfully.",
    "data": { "id": 1, "title": "...", "instructors": [...], "partner_institution": {...}, ... }
}
```

**Error:**
```json
{
    "success": false,
    "message": "Course is 'under_review' and cannot be edited. ..."
}
```

The `data.instructors` array items have shape: `{ "id": 5, "full_name": "Alice", "email": "alice@..." }`.
The `data.created_by` field has the same shape.
The `data.partner_institution` field has shape `{ "id": 3, "institution_name": "...", "slug": "..." }` or `null` for instructor-created courses.

---

## Partner Institution Course Creation

Partner institutions create courses using the same endpoint (`POST /api/v1/courses/create/`) but with a `partner_institution` user token. The `partner_institution` FK on the course is auto-set by the server — it is NOT sent in the request body.

```
POST {{base_url}}/courses/create/
Authorization: {{partner_institution_token}}
Content-Type: application/json

{
    "title": "Institution Course",
    "description": "Created by partner institution.",
    "instructors": [{{instructor_id}}]
}
```

**Expected:** `201 Created`, `data.partner_institution` is populated with the institution's details, `data.instructors` contains the passed instructor(s). The partner institution user is NOT in `data.instructors` — they are identified only via `data.created_by`.
