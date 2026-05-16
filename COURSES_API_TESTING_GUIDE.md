# Courses and Quizzes API Testing Guide (Postman)

## Table of Contents

1. [Base URLs](#1-base-urls)
2. [Prerequisites](#2-prerequisites)
3. [Postman Environment Variables](#3-postman-environment-variables)
4. [Required Headers](#4-required-headers)
5. [Course API Flow (with demo data)](#5-course-api-flow-with-demo-data)
6. [Course Metadata: Learning Objectives, Prerequisites, Audiences](#6-course-metadata-learning-objectives-prerequisites-audiences)
7. [Section API Flow](#7-section-api-flow)
8. [Section Content API (the only creation path for all content)](#8-section-content-api-the-only-creation-path-for-all-content)
9. [Lecture Endpoints (read / update / delete)](#9-lecture-endpoints-read--update--delete)
10. [Quiz API Flow](#10-quiz-api-flow)
11. [Assignment API Flow](#11-assignment-api-flow)
12. [Coding Exercise API Flow](#12-coding-exercise-api-flow)
13. [Course Status Transitions](#13-course-status-transitions)
14. [Common Error Responses You Should Test](#14-common-error-responses-you-should-test)
15. [Quick Manual End-to-End Scenario](#15-quick-manual-end-to-end-scenario)
16. [Notes](#16-notes)

## 1) Base URLs
- Courses base: `http://127.0.0.1:8000/api/v1/courses`
- Auth base: `http://127.0.0.1:8000/api/v1/auth`

## 2) Prerequisites
1. Install dependencies:
```bash
pip install -r requirements.txt
```
2. Configure environment (`.env`) for media and video processing:
```env
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
MEDIA_URL=/media/
MEDIA_ROOT=<absolute-path-to-project>/media
FFMPEG_BINARY_PATH=<absolute-path-to-ffmpeg>
FFPROBE_BINARY_PATH=<absolute-path-to-ffprobe>
```
3. Run services:
```bash
python manage.py runserver
celery -A career_college_backend worker --loglevel=info --pool=solo 
```
4. Login with an instructor account and copy JWT access token.

## 3) Postman Environment Variables
Create a Postman environment with:
```text
base_url=http://127.0.0.1:8000/api/v1/courses
access_token=<jwt-access-token>
course_id=
section_id=
lecture_id=
content_id=
quiz_id=
question_id=
answer_id=
exercise_id=
config_id=
tc_id=
assignment_id=
aq_id=
objective_id=
prerequisite_id=
audience_id=
```

## 4) Required Headers
Use these headers unless noted otherwise:
```http
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

## 5) Course API Flow (with demo data)

### 5.1 Create Course
- Method: `POST`
- URL: `{{base_url}}/create/`
- Body:
```json
{
  "title": "Python Backend Bootcamp",
  "description": "Build production APIs with Django and DRF.",
  "price": "79.99",
  "language": "English",
  "level": "intermediate",
  "duration_minutes": 240,
  "category": 1
}
```
- Expected status: `201`
- Expected response shape:
```json
{
  "success": true,
  "message": "Course created successfully.",
  "data": {
    "id": 101,
    "title": "Python Backend Bootcamp"
  }
}
```
- Save `data.id` as `course_id`.

### 5.2 List Courses
- Method: `GET`
- URL: `{{base_url}}/`
- Expected status: `200`

### 5.3 Get Course Detail
- Method: `GET`
- URL: `{{base_url}}/{{course_id}}/`
- Expected status: `200`

### 5.4 Patch Course
- Method: `PATCH`
- URL: `{{base_url}}/{{course_id}}/`
- Body:
```json
{
  "title": "Python Backend Bootcamp (Updated)",
  "price": "89.99"
}
```
- Expected status: `200`
- Note: `status` and `rejection_reason` are not writable via PATCH. Use the dedicated transition endpoints in section 13.

## 6) Course Metadata: Learning Objectives, Prerequisites, Audiences

These three resources share an identical contract — same fields, same response shape, same ownership and editable-state rules. Only the URL segment changes:

| Resource | List/Create URL | Detail URL |
|---|---|---|
| Learning objectives | `{{base_url}}/{{course_id}}/learning-objectives/` | `{{base_url}}/learning-objectives/{{objective_id}}/` |
| Prerequisites | `{{base_url}}/{{course_id}}/prerequisites/` | `{{base_url}}/prerequisites/{{prerequisite_id}}/` |
| Audiences | `{{base_url}}/{{course_id}}/audiences/` | `{{base_url}}/audiences/{{audience_id}}/` |

**Common rules:**
- All write actions require an authenticated JWT for an instructor who is in `course.instructors`. Non-owners get `404` (not `403`), so the existence of the course/item is not leaked.
- Writes (`POST`, `PATCH`, `PUT`, `DELETE`) only succeed while the course is editable (`draft` or `rejected`). On `published` / `under_review` / `archived`, writes return the `guard_editable` error.
- Each item has one unique constraint per course: `(course, text)`. Submitting duplicate text returns `400` with `"<Resource> already exists for this course."`
- List results are ordered by `display_order, id`. Supports `?ordering=display_order` and `?ordering=-display_order`.

The examples below use **learning objectives**; substitute the URL segment to test prerequisites and audiences.

### 6.1 Create a Learning Objective
- Method: `POST`
- URL: `{{base_url}}/{{course_id}}/learning-objectives/`
- Body:
```json
{
  "text": "Design RESTful endpoints using Django REST Framework.",
  "display_order": 1
}
```
- Expected status: `201`
- Expected response:
```json
{
  "success": true,
  "message": "Learning objective created successfully.",
  "data": {
    "id": 12,
    "text": "Design RESTful endpoints using Django REST Framework.",
    "display_order": 1
  }
}
```
- Save `data.id` as `objective_id`.
- `display_order` is optional and defaults to `0`. `text` is required and trimmed; whitespace-only values are rejected.

### 6.2 List Learning Objectives for a Course
- Method: `GET`
- URL: `{{base_url}}/{{course_id}}/learning-objectives/`
- Optional query: `?ordering=display_order` or `?ordering=-display_order`
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "data": [
    { "id": 12, "text": "Design RESTful endpoints…", "display_order": 1 },
    { "id": 13, "text": "Write integration tests for Django views.", "display_order": 2 }
  ]
}
```

### 6.3 Get a Single Learning Objective
- Method: `GET`
- URL: `{{base_url}}/learning-objectives/{{objective_id}}/`
- Expected status: `200`

### 6.4 Patch (Partial Update)
- Method: `PATCH`
- URL: `{{base_url}}/learning-objectives/{{objective_id}}/`
- Body (any subset of `text`, `display_order`):
```json
{ "display_order": 3 }
```
- Expected status: `200`
- Message: `"Learning objective updated successfully."`

### 6.5 Put (Replace)
- Method: `PUT`
- URL: `{{base_url}}/learning-objectives/{{objective_id}}/`
- Body (all writable fields):
```json
{
  "text": "Design RESTful endpoints (revised).",
  "display_order": 1
}
```
- Expected status: `200`
- Message: `"Learning objective replaced successfully."`

### 6.6 Delete
- Method: `DELETE`
- URL: `{{base_url}}/learning-objectives/{{objective_id}}/`
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "message": "Learning objective deleted successfully."
}
```

### 6.7 Same Flow for Prerequisites
- Create: `POST {{base_url}}/{{course_id}}/prerequisites/`
```json
{ "text": "Basic Python syntax and functions.", "display_order": 1 }
```
- List: `GET {{base_url}}/{{course_id}}/prerequisites/`
- Detail / patch / put / delete: `{{base_url}}/prerequisites/{{prerequisite_id}}/`
- Messages: `"Prerequisite created/updated/replaced/deleted successfully."`

### 6.8 Same Flow for Audiences
- Create: `POST {{base_url}}/{{course_id}}/audiences/`
```json
{ "text": "Backend developers transitioning to Django.", "display_order": 1 }
```
- List: `GET {{base_url}}/{{course_id}}/audiences/`
- Detail / patch / put / delete: `{{base_url}}/audiences/{{audience_id}}/`
- Messages: `"Audience created/updated/replaced/deleted successfully."`

### 6.9 Validation & Error Cases

**Empty / whitespace-only `text`**
- Body: `{ "text": "   " }`
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": { "text": ["Text cannot be empty."] }
}
```

**Missing `text` on create**
- Body: `{ "display_order": 1 }`
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": { "text": ["This field is required."] }
}
```

**Duplicate `text` for the same course (unique constraint)**
- POST the same `text` value twice under the same `course_id`.
- Expected status: `400`
- Example response (resource label varies):
```json
{
  "success": false,
  "message": "Learning objective already exists for this course."
}
```

**Edit while course is not editable**
- The course is `under_review`, `published`, or `archived`.
- Any write (`POST`, `PATCH`, `PUT`, `DELETE`) is rejected by `guard_editable`.
- Expected status: `422`
- Example response:
```json
{
  "success": false,
  "message": "Course is not editable in its current status."
}
```

**Non-owner instructor**
- Authenticated as a verified instructor who is **not** in `course.instructors`.
- Any method on the list or detail endpoint returns `404` (existence is not leaked).

**Unauthenticated**
- Omit the `Authorization` header on any endpoint.
- Expected status: `401`.

### 6.10 Bulk-Set via Course Update
- The course create/update endpoints accept `learning_objectives`, `prerequisites`, and `audiences` as nested arrays. Supplying any of these on `PATCH {{base_url}}/{{course_id}}/` **replaces the entire set** for that course (delete + re-insert). Use the dedicated endpoints above for incremental edits.
- Example body for `PATCH {{base_url}}/{{course_id}}/`:
```json
{
  "learning_objectives": [
    { "text": "Build production REST APIs.", "display_order": 1 },
    { "text": "Containerize with Docker.", "display_order": 2 }
  ],
  "prerequisites": [
    { "text": "Comfortable with Python.", "display_order": 1 }
  ],
  "audiences": [
    { "text": "Backend engineers.", "display_order": 1 }
  ]
}
```

## 7) Section API Flow

### 7.1 Create Section
- Method: `POST`
- URL: `{{base_url}}/{{course_id}}/sections/create/`
- Body:
```json
{
  "title": "Getting Started",
  "description": "Core setup and project structure",
  "position": 1
}
```
- Expected status: `201`
- Save `data.id` as `section_id`.

### 7.2 List Sections
- Method: `GET`
- URL: `{{base_url}}/{{course_id}}/sections/`
- Optional query params:
  - `ordering=position`
  - `ordering=-position`

### 7.3 Get / Update / Delete Section
- `GET {{base_url}}/sections/{{section_id}}/`
- `PATCH {{base_url}}/sections/{{section_id}}/`
- `PUT {{base_url}}/sections/{{section_id}}/`
- `DELETE {{base_url}}/sections/{{section_id}}/`

## 8) Section Content API (the only creation path for all content)

All lectures, quizzes, coding exercises, and assignments must be created through this endpoint. There are no separate creation endpoints for individual content types.

### 8.1 Create Article Lecture
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Body:
```json
{
  "item_type": "lecture",
  "title": "REST Fundamentals",
  "lecture_type": "article",
  "article_content": "HTTP methods, status codes, and API design basics.",
  "position": 1
}
```
- Expected status: `201`
- Expected response shape:
```json
{
  "success": true,
  "message": "Lecture created successfully.",
  "data": {
    "id": 201,
    "section": 11,
    "item_type": "lecture",
    "object_id": 301,
    "position": 1,
    "content": {
      "id": 301,
      "title": "REST Fundamentals",
      "lecture_type": "article"
    }
  }
}
```
- Save `data.object_id` as `lecture_id`.

### 8.1b Create Video Lecture (multipart/form-data)
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Body type: `form-data`
  - `item_type` = `lecture`
  - `title` = `Intro Video`
  - `lecture_type` = `video`
  - `video_file` = (select file)
  - `position` = `2` (optional)
- Expected status: `201`
- After creation the video is queued for transcoding. Poll `GET {{base_url}}/lectures/{{lecture_id}}/` until `active_video_asset.status` is `ready` or `failed`.

### 8.2 Create Quiz via Section Content
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Body:
```json
{
  "item_type": "quiz",
  "title": "REST Basics Quiz",
  "description": "Checks understanding of HTTP and endpoints.",
  "position": 2
}
```
- Expected status: `201`
- Save:
  - `data.id` as `content_id` (section content row id)
  - `data.object_id` as `quiz_id` (actual quiz id)

### 8.2b Create Coding Exercise via Section Content
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Body:
```json
{
  "item_type": "coding",
  "title": "Reverse a String",
  "description": "Practice string manipulation.",
  "problem_statement": "Given a string s, return the string reversed.",
  "difficulty": "easy",
  "default_language": "python",
  "supported_languages": ["python", "javascript"],
  "time_limit_ms": 2000,
  "position": 3
}
```
- Expected status: `201`
- Expected response shape:
```json
{
  "success": true,
  "message": "Coding exercise created successfully.",
  "data": {
    "id": 501,
    "section": 11,
    "item_type": "coding",
    "object_id": 1,
    "position": 3,
    "content": {
      "id": 1,
      "title": "Reverse a String",
      "difficulty": "easy",
      "default_language": "python"
    }
  }
}
```
- Save `data.object_id` as `exercise_id`.

### 8.3 List Ordered Curriculum
- Method: `GET`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Expected status: `200`
- Returns all content items (lectures, quizzes, coding exercises, assignments) ordered by `position`.

### 8.4 Reorder Curriculum Item
- Method: `PATCH`
- URL: `{{base_url}}/contents/{{content_id}}/reorder/`
- Body:
```json
{
  "position": 1
}
```
- Expected status: `200`
- Expected behavior:
  - Item moves to target position.
  - Other items shift automatically.
  - No need for empty target slots.

## 9) Lecture Endpoints (read / update / delete)

Create lectures via `sections/{id}/contents/` (section 8). These endpoints are for reading and modifying existing lectures.

### 9.1 List Lectures in a Section
- `GET {{base_url}}/sections/{{section_id}}/lectures/`

### 9.2 Get / Patch / Put / Delete Lecture
- `GET {{base_url}}/lectures/{{lecture_id}}/`
- `PATCH {{base_url}}/lectures/{{lecture_id}}/`
  - Demo patch body:
```json
{
  "title": "REST Fundamentals (Updated)"
}
```
- `PUT {{base_url}}/lectures/{{lecture_id}}/`
- `DELETE {{base_url}}/lectures/{{lecture_id}}/`

## 10) Quiz API Flow

Create quizzes via `sections/{id}/contents/` (section 8.2). These endpoints are for reading and modifying existing quizzes and their questions/answers.

### 10.1 Get / Patch / Delete Quiz
- `GET {{base_url}}/quizzes/{{quiz_id}}/`
- `PATCH {{base_url}}/quizzes/{{quiz_id}}/`
  - Demo patch body:
```json
{
  "title": "Django ORM Quiz (Updated)"
}
```
- `DELETE {{base_url}}/quizzes/{{quiz_id}}/`

### 10.2 Create Quiz Question
- Method: `POST`
- URL: `{{base_url}}/quizzes/{{quiz_id}}/questions/`
- Body:
```json
{
  "question_text": "Which method returns exactly one object and throws if missing?",
  "position": 1
}
```
- Expected status: `201`
- Save `data.id` as `question_id`.

### 10.3 List Quiz Questions
- Method: `GET`
- URL: `{{base_url}}/quizzes/{{quiz_id}}/questions/`

### 10.4 Update/Delete Question
- `PATCH {{base_url}}/quiz-questions/{{question_id}}/`
- `DELETE {{base_url}}/quiz-questions/{{question_id}}/`

### 10.5 Create Quiz Answers
- Method: `POST`
- URL: `{{base_url}}/quiz-questions/{{question_id}}/answers/`
- Body (correct):
```json
{
  "answer_text": "get()",
  "is_correct": true
}
```
- Body (incorrect option):
```json
{
  "answer_text": "filter()",
  "is_correct": false
}
```
- Save first created answer id as `answer_id`.

### 10.6 List / Update / Delete Answers
- `GET {{base_url}}/quiz-questions/{{question_id}}/answers/`
- `PATCH {{base_url}}/quiz-answers/{{answer_id}}/`
- `DELETE {{base_url}}/quiz-answers/{{answer_id}}/`

## 11) Assignment API Flow

Assignments are open-ended (free-text) questions with instructor-provided model answers. Like lectures, quizzes, and coding exercises, an assignment **must be created through the section-content endpoint** (`sections/{id}/contents/` with `item_type: "assignment"`). The dedicated `/assignments/...` URLs handle list / read / update / delete and the question sub-resource.

All write endpoints require a verified-instructor JWT. `model_answer` on a question is instructor-only and is stripped from learner-facing responses (Part 2 enforcement).

### 11.1 Create Assignment via Section Content
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Body:
```json
{
  "item_type": "assignment",
  "title": "Reflection Essay",
  "description": "Reflect on the REST fundamentals lecture.",
  "instructions": "Write at least 300 words. Cite at least one example.",
  "passing_score": 60,
  "position": 4
}
```
- Expected status: `201`
- Expected response shape:
```json
{
  "success": true,
  "message": "Assignment created successfully.",
  "data": {
    "id": 207,
    "section": 11,
    "item_type": "assignment",
    "object_id": 1,
    "position": 4,
    "content": {
      "id": 1,
      "title": "Reflection Essay",
      "passing_score": 60
    }
  }
}
```
- Save:
  - `data.id` as `content_id` (the section-content slot id)
  - `data.object_id` as `assignment_id` (the actual assignment id)

### 11.2 List Assignments in a Section
- Method: `GET`
- URL: `{{base_url}}/sections/{{section_id}}/assignments/`
- Expected status: `200`
- Returns assignments belonging to that section, newest first. Each row includes nested `questions` and a computed `max_score` (sum of question points).

### 11.3 Get Assignment Detail
- Method: `GET`
- URL: `{{base_url}}/assignments/{{assignment_id}}/`
- Expected status: `200`
- Expected response shape:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "section_id": 11,
    "title": "Reflection Essay",
    "description": "Reflect on the REST fundamentals lecture.",
    "instructions": "Write at least 300 words. Cite at least one example.",
    "passing_score": 60,
    "max_score": 0,
    "questions": [],
    "created_at": "2026-05-06T05:33:41Z",
    "updated_at": "2026-05-06T05:33:41Z"
  }
}
```

> **Note:** the dedicated assignment endpoint **does not accept POST**. Sending `POST {{base_url}}/sections/{{section_id}}/assignments/` returns `405 Method Not Allowed`. Always create through `sections/{id}/contents/` (section 11.1).

### 11.4 Patch Assignment
- Method: `PATCH`
- URL: `{{base_url}}/assignments/{{assignment_id}}/`
- Body:
```json
{
  "title": "Reflection Essay (Updated)",
  "passing_score": 70
}
```
- Expected status: `200`
- Allowed partial-update fields: `title`, `description`, `instructions`, `passing_score`.

### 11.5 Delete Assignment
- Method: `DELETE`
- URL: `{{base_url}}/assignments/{{assignment_id}}/`
- Expected status: `200`
- Example response:
```json
{
  "success": true,
  "message": "Assignment deleted successfully."
}
```
- Expected behavior: deletes the assignment, cascades all its questions, and removes its `SectionContent` slot automatically (cascade via `GenericRelation`).

---

### 11.6 Add Assignment Question
- Method: `POST`
- URL: `{{base_url}}/assignments/{{assignment_id}}/questions/`
- Body:
```json
{
  "question_text": "What surprised you most about REST design?",
  "model_answer": "Reference reflection: idempotency boundaries, statelessness trade-offs.",
  "points": 10,
  "hint": "Reference at least one HTTP verb."
}
```
- Expected status: `201`
- Expected response shape:
```json
{
  "success": true,
  "message": "Question created successfully.",
  "data": {
    "id": 1,
    "assignment_id": 1,
    "question_text": "What surprised you most about REST design?",
    "model_answer": "Reference reflection: idempotency boundaries, statelessness trade-offs.",
    "points": 10,
    "hint": "Reference at least one HTTP verb.",
    "position": 1
  }
}
```
- Save `data.id` as `aq_id`.
- `position` is server-assigned (next available slot for that assignment) — do not send it in the body.

### 11.7 List Assignment Questions
- Method: `GET`
- URL: `{{base_url}}/assignments/{{assignment_id}}/questions/`
- Expected status: `200`
- Results are ordered by `position`.

### 11.8 Get / Patch / Delete Assignment Question
- `GET  {{base_url}}/assignment-questions/{{aq_id}}/`
- `PATCH {{base_url}}/assignment-questions/{{aq_id}}/`
  - Demo patch body:
```json
{
  "model_answer": "Updated reference answer.",
  "points": 15
}
```
  - Expected status: `200`
  - Allowed partial-update fields: `question_text`, `model_answer`, `points`, `hint`.
- `DELETE {{base_url}}/assignment-questions/{{aq_id}}/`
  - Expected status: `200`
  - Example response:
```json
{
  "success": true,
  "message": "Question deleted successfully."
}
```
  - Expected behavior: deletes the question and compacts trailing positions (e.g., positions `1, 2, 3` → delete `2` → `1, 2`).

### 11.9 Reorder Assignment Questions
- Method: `PATCH`
- URL: `{{base_url}}/assignments/{{assignment_id}}/questions/reorder/`
- Body:
```json
{
  "ordered_ids": [3, 1, 2]
}
```
- Expected status: `200`
- Expected behavior: positions are reassigned to match the order of `ordered_ids` (here: question `3` → position `1`, question `1` → position `2`, question `2` → position `3`).

---

### 11.10 Assignment Validation Error Cases

**Title too short**
- Body:
```json
{
  "item_type": "assignment",
  "title": "A"
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "title": ["Assignment title must be at least 2 characters long."]
  }
}
```

**Title missing**
- Body:
```json
{
  "item_type": "assignment"
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "title": ["This field is required."]
  }
}
```

**Empty question text**
- POST to `assignments/{{assignment_id}}/questions/` with:
```json
{
  "question_text": "   "
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "question_text": ["Question text cannot be empty."]
  }
}
```

**Reorder with mismatched IDs**
- Assignment has questions `[1, 2, 3]`. Body:
```json
{
  "ordered_ids": [1, 2, 99]
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "ordered_ids must match the questions belonging to this assignment."
}
```

**Reorder with duplicate IDs**
- Body:
```json
{
  "ordered_ids": [1, 1, 2]
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "ordered_ids contains duplicates."
}
```

**Reorder with empty list**
- Body:
```json
{
  "ordered_ids": []
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "ordered_ids must be a non-empty list."
}
```

**Reorder with non-integer IDs**
- Body:
```json
{
  "ordered_ids": ["abc", "def"]
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "ordered_ids must contain integers only."
}
```

---

### 11.11 Assignment Auth & Ownership Error Cases

**Unauthenticated create**
- POST `sections/{{section_id}}/contents/` with `item_type: "assignment"` and **no** `Authorization` header.
- Expected status: `401`

**Unverified instructor tries to create**
- Authenticated as an instructor whose `InstructorProfile.is_verified` is `false`.
- Expected status: `403`
- Example response:
```json
{
  "success": false,
  "message": "Only verified instructors can perform this action.",
  "detail": "Only verified instructors can perform this action."
}
```

**Learner tries to create**
- Authenticated as a `learner` user.
- Expected status: `403`

**Verified instructor not on the course**
- Authenticated as a verified instructor who is **not** in `course.instructors`.
- POST `sections/{{section_id}}/contents/` for that course's section.
- Expected status: `404`
- Example response:
```json
{
  "success": false,
  "detail": "No CourseSection matches the given query."
}
```

**Cross-instructor read of someone else's assignment**
- `GET {{base_url}}/assignments/{{assignment_id}}/` while authenticated as an instructor not on that course.
- Expected status: `404` (the API returns 404 instead of 403 to avoid leaking the existence of resources you don't own).

**Unverified instructor patches an assignment they own**
- An unverified instructor on the course can `GET` but not `PATCH`/`PUT`/`DELETE`.
- Expected status on patch: `403`.

**POST to dedicated assignment list endpoint**
- `POST {{base_url}}/sections/{{section_id}}/assignments/`
- Expected status: `405 Method Not Allowed`. Use `sections/{id}/contents/` instead.

## 12) Coding Exercise API Flow

Create coding exercises via `sections/{id}/contents/` (section 8.2b). All endpoints below require a verified instructor JWT. The exercise must belong to a section of a course you instruct — otherwise the API returns `404`.

### 12.1 Get Coding Exercise Detail
- Method: `GET`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/`
- Expected status: `200`

### 12.2 Patch Coding Exercise
- Method: `PATCH`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/`
- Body:
```json
{
  "difficulty": "hard",
  "time_limit_ms": 5000
}
```
- Expected status: `200`

### 12.3 Delete Coding Exercise
- Method: `DELETE`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/`
- Expected status: `204`
- Expected behavior: deletes the exercise and its `SectionContent` slot automatically (cascade via `GenericRelation`).

---

### 12.4 Add Language Config
- Method: `POST`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/language-configs/`
- Body:
```json
{
  "language": "python",
  "starter_code": "def two_sum(nums, target):\n    pass",
  "solution_code": "def two_sum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        if target - n in seen:\n            return [seen[target - n], i]\n        seen[n] = i"
}
```
- Expected status: `201`
- Save `data.id` as `config_id`.
- Valid `language` values: `python`, `javascript`, `cpp`, `java`.

### 12.5 Duplicate Language Config (error case)
- Repeat the same POST above with `"language": "python"`.
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "A config for this language already exists on this exercise."
}
```

### 12.6 List Language Configs
- Method: `GET`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/language-configs/`
- Expected status: `200`

### 12.7 Get / Patch / Delete Language Config
- `GET  {{base_url}}/coding-exercises/{{exercise_id}}/language-configs/{{config_id}}/`
- `PATCH {{base_url}}/coding-exercises/{{exercise_id}}/language-configs/{{config_id}}/`
  - Demo patch body:
```json
{
  "starter_code": "def two_sum(nums, target):\n    # your code here\n    pass"
}
```
- `DELETE {{base_url}}/coding-exercises/{{exercise_id}}/language-configs/{{config_id}}/`

---

### 12.8 Add Test Case
- Method: `POST`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/testcases/`
- Body (visible case):
```json
{
  "input_data": "[2,7,11,15]\n9",
  "expected_output": "[0,1]",
  "is_hidden": false,
  "explanation": "nums[0] + nums[1] == 9",
  "position": 1
}
```
- Expected status: `201`
- Save `data.id` as `tc_id`.

- Body (hidden/grading-only case):
```json
{
  "input_data": "[3,2,4]\n6",
  "expected_output": "[1,2]",
  "is_hidden": true,
  "explanation": "",
  "position": 2
}
```

### 12.9 Duplicate Test Case Position (error case)
- Repeat POST with `"position": 1` for the same exercise.
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "A test case already exists at that position for this exercise."
}
```

### 12.10 List Test Cases
- Method: `GET`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/testcases/`
- Expected status: `200`
- Results are ordered by `position`.

### 12.11 Get / Patch / Delete Test Case
- `GET  {{base_url}}/coding-exercises/{{exercise_id}}/testcases/{{tc_id}}/`
- `PATCH {{base_url}}/coding-exercises/{{exercise_id}}/testcases/{{tc_id}}/`
  - Demo patch body:
```json
{
  "is_hidden": true
}
```
- `DELETE {{base_url}}/coding-exercises/{{exercise_id}}/testcases/{{tc_id}}/`

---

### 12.12 Coding Exercise Validation Error Cases

**default_language not in supported_languages**
- Body:
```json
{
  "section": {{section_id}},
  "title": "Bad Exercise",
  "problem_statement": "...",
  "default_language": "cpp",
  "supported_languages": ["python", "javascript"]
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "default_language": ["default_language must be in supported_languages."]
  }
}
```

**Empty supported_languages list**
- Body includes `"supported_languages": []`
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "supported_languages": ["supported_languages must be a non-empty list."]
  }
}
```

**Invalid language value**
- Body includes `"supported_languages": ["python", "ruby"]`
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "supported_languages": ["Invalid languages: ['ruby']. Must be one of ['python', 'javascript', 'cpp', 'java']."]
  }
}
```

**Title too short**
- Body includes `"title": "AB"`
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "title": ["Title must be at least 3 characters long."]
  }
}
```

## 13) Course Status Transitions

### 13.1 Submit Course for Review (Instructor)
- Method: `POST`
- URL: `{{base_url}}/{{course_id}}/submit/`
- Body: _(none required)_
- Headers: verified instructor JWT
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "message": "Course submitted for review.",
  "data": { "id": 101, "status": "under_review" }
}
```
- Will return `400` with an `errors` dict if completeness checks fail (missing title/description, empty section, pending videos, incomplete quizzes).

### 13.2 Admin Approve Course
- Method: `POST`
- URL: `{{base_url}}/{{course_id}}/review/`
- Headers: admin JWT (`is_staff` or `user_type: admin`)
- Body:
```json
{ "action": "approve" }
```
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "message": "Course approved successfully.",
  "data": { "id": 101, "status": "published" }
}
```

### 13.3 Admin Reject Course
- Method: `POST`
- URL: `{{base_url}}/{{course_id}}/review/`
- Headers: admin JWT
- Body:
```json
{
  "action": "reject",
  "rejection_reason": "Missing captions on lecture 3. Please add subtitles."
}
```
- Expected status: `200`
- `rejection_reason` is **required** when action is `reject`. Omitting it returns `400`.

### 13.4 Instructor Rework a Rejected Course (back to Draft)
- Method: `POST`
- URL: `{{base_url}}/{{course_id}}/rework/`
- Headers: verified instructor JWT (must be assigned to the course)
- Body: _(none required)_
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "message": "Course moved back to draft for reworking.",
  "data": { "id": 101, "status": "draft" }
}
```
- Only works when current status is `rejected`. Any other status returns `400`.

### 13.5 Archive a Published Course
- Method: `POST`
- URL: `{{base_url}}/{{course_id}}/archive/`
- Headers: instructor or admin JWT
- Body: _(none required)_
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "message": "Course archived successfully.",
  "data": { "id": 101, "status": "archived" }
}
```
- Only works when current status is `published`.

### 13.6 Invalid Transition (error case)
- Attempt to call `/submit/` on a course that is already `under_review`.
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Cannot transition from \"under_review\" to \"under_review\". Allowed: published, rejected."
}
```

## 14) Common Error Responses You Should Test

### 14.1 Invalid `item_type` in section contents
- Request:
```json
{
  "item_type": "video",
  "title": "Invalid Type"
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "item_type must be 'lecture', 'quiz', 'coding', or 'assignment'."
}
```

### 14.2 Invalid reorder position
- Request:
```json
{
  "position": 0
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "position must be a positive integer."
}
```

### 14.3 Two correct answers for one question
- Try creating a second answer with `"is_correct": true` for the same question.
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "is_correct": [
      "A correct answer already exists for this question."
    ]
  }
}
```

## 15) Quick Manual End-to-End Scenario
1. Create course → save `course_id`.
2. Add 1-2 learning objectives, prerequisites, and audiences under that course (section 6 endpoints).
3. Create section → save `section_id`.
4. Create one article lecture via `sections/{id}/contents/` (`item_type: "lecture"`).
5. Create one quiz via `sections/{id}/contents/` (`item_type: "quiz"`) → save `quiz_id`.
6. Create one assignment via `sections/{id}/contents/` (`item_type: "assignment"`) → save `assignment_id`.
7. Create one coding exercise via `sections/{id}/contents/` (`item_type: "coding"`) → save `exercise_id`.
8. List `sections/{id}/contents/` — verify all four items appear with correct `content` summaries.
9. Reorder coding exercise to position `1`; verify list updates with shifted items.
10. Add one question and two answers (one correct) to the quiz.
11. Add three questions to the assignment (`POST assignments/{id}/questions/`); reorder them via `assignments/{id}/questions/reorder/`; `GET assignments/{id}/` and verify `max_score` equals the sum of question points.
12. Add a Python language config to the exercise (`POST coding-exercises/{id}/language-configs/`).
13. Add two test cases (one visible, one hidden) to the exercise.
14. `GET coding-exercises/{id}/` — verify `language_configs` and `test_cases` arrays are populated.
15. Patch exercise difficulty to `"hard"` and verify update.
16. Delete the exercise — re-fetch `sections/{id}/contents/` and confirm the coding slot is gone.
17. Delete the assignment — re-fetch `sections/{id}/contents/` and confirm the assignment slot is gone (its questions cascade away too).
18. Call `POST {{base_url}}/{{course_id}}/submit/` — expect `400` because the section now has reduced content. Re-add content, then re-submit — expect `200` with `status: under_review`.
19. As an admin, call `POST {{base_url}}/{{course_id}}/review/` with `{"action": "approve"}` — expect `status: published`.
20. Call `POST {{base_url}}/{{course_id}}/archive/` — expect `status: archived`.

## 16) Notes
- All ownership checks are instructor-scoped; if the course/section/exercise/assignment is not yours, API returns `404`.
- Status transitions (submit, review, rework, archive) are dedicated POST endpoints. Do not set `status` directly via PATCH — the field is not writable that way.
- `solution_code` on language configs is stored server-side and must never appear in learner-facing responses (Part 2 enforcement).
- Hidden test cases (`is_hidden: true`) are for grading only and must never be exposed to learners (Part 2 enforcement).
- `model_answer` on assignment questions is instructor-only and is stripped from learner-facing responses (Part 2 enforcement).
- For video lectures, transcoding states usually move from `processing` to `ready` (or `failed` on error).
- Assignments, lectures, quizzes, and coding exercises share one ordering layer (`SectionContent`); reorder via `PATCH contents/{content_id}/reorder/` regardless of item type.
