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
12B. [Learner Consumption Endpoints (`/learn/...`)](#12b-learner-consumption-endpoints-learn) — includes assignment auto-grading (12B.10–12B.14)
12C. [My-Courses Endpoints (course header + dashboard)](#12c-my-courses-endpoints-course-header--dashboard)
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
  "total_score": 100,
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
      "total_score": 100,
      "passing_score": 60
    }
  }
}
```
- Save:
  - `data.id` as `content_id` (the section-content slot id)
  - `data.object_id` as `assignment_id` (the actual assignment id)

**Field semantics:**

- `total_score` is the instructor-declared "this assignment is worth N points" value. It's the denominator the learner sees and the figure `passing_score` is measured against.
- `passing_score` must be `<= total_score`. Mismatch → `400` with `errors.passing_score`.
- `total_score` is **independent** of the sum of `question.points`. The questions are a sub-allocation guide — the authoring UI can compare `total_score` against the response's `max_score` (sum-of-question-points) to flag under-/over-funded rubrics. A learner can never score more than `sum(question.points)`, so the instructor should keep the two roughly aligned.

### 11.2 List Assignments in a Section
- Method: `GET`
- URL: `{{base_url}}/sections/{{section_id}}/assignments/`
- Expected status: `200`
- Returns assignments belonging to that section, newest first. Each row includes nested `questions`, the instructor-declared `total_score`, and a computed `max_score` (sum of `question.points`).

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
    "total_score": 100,
    "passing_score": 60,
    "max_score": 0,
    "questions": [],
    "created_at": "2026-05-06T05:33:41Z",
    "updated_at": "2026-05-06T05:33:41Z"
  }
}
```

`total_score` is the declared total; `max_score` is the sum of `question.points`. They are distinct on purpose — see 11.1.

> **Note:** the dedicated assignment endpoint **does not accept POST**. Sending `POST {{base_url}}/sections/{{section_id}}/assignments/` returns `405 Method Not Allowed`. Always create through `sections/{id}/contents/` (section 11.1).

### 11.4 Patch Assignment
- Method: `PATCH`
- URL: `{{base_url}}/assignments/{{assignment_id}}/`
- Body:
```json
{
  "title": "Reflection Essay (Updated)",
  "total_score": 120,
  "passing_score": 70
}
```
- Expected status: `200`
- Allowed partial-update fields: `title`, `description`, `instructions`, `total_score`, `passing_score`.
- Cross-field rule on partials: if you change only one side, the validator uses the existing value of the other. Updating `passing_score` alone to a value greater than the stored `total_score` → `400`.

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
  "hint": "Reference at least one HTTP verb.",
  "rubric": [
    {
      "type": "keyword",
      "value": "idempotency",
      "points": 4,
      "feedback_on_match": "Correctly identifies idempotency.",
      "feedback_on_miss": "Missing the concept of idempotency."
    },
    {
      "type": "any_of",
      "value": ["GET", "POST", "PUT", "PATCH", "DELETE"],
      "points": 2,
      "feedback_on_match": "Mentions an HTTP verb.",
      "feedback_on_miss": "No HTTP verb referenced."
    },
    {
      "type": "min_length",
      "value": 80,
      "points": 4,
      "feedback_on_match": "Answer is detailed enough.",
      "feedback_on_miss": "Answer is too short — aim for at least 80 characters."
    }
  ]
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
    "rubric": [
      {"type": "keyword", "value": "idempotency", "points": 4, "feedback_on_match": "...", "feedback_on_miss": "..."},
      {"type": "any_of",  "value": ["GET","POST","PUT","PATCH","DELETE"], "points": 2, "feedback_on_match": "...", "feedback_on_miss": "..."},
      {"type": "min_length", "value": 80, "points": 4, "feedback_on_match": "...", "feedback_on_miss": "..."}
    ],
    "points": 10,
    "hint": "Reference at least one HTTP verb.",
    "position": 1
  }
}
```
- Save `data.id` as `aq_id`.
- `position` is server-assigned (next available slot for that assignment) — do not send it in the body.

**Rubric authoring rules (enforced by the serializer):**

- `sum(criterion.points)` must equal `question.points`. Mismatch → `400`.
- Supported `type` values: `keyword`, `regex`, `min_length`, `max_length`, `any_of`, `all_of`. Unknown `type` → `400`.
- `regex` criteria are compiled at save time; an unparseable pattern → `400`.
- `case_sensitive` (boolean, optional) is only honoured by `keyword` and `regex`; defaults to `false`.
- An empty rubric (`"rubric": []`) is allowed during draft authoring but will produce a `score=0` submission once the course is published — fill it out before submitting the course for review.
- `rubric` is **instructor-only** in the response. The same endpoint called by a non-instructor would omit it (along with `model_answer`).

**Example error — points mismatch:**
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "non_field_errors": ["sum of criterion.points (7) must equal question.points (10)."]
  }
}
```

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

## 12B) Learner Consumption Endpoints (`/learn/...`)

Phase-1 of the split learner surface: a lightweight curriculum outline + a per-lecture detail endpoint + an idempotent watch-progress write. Quiz and assignment consumption are Phase-2 and not yet implemented.

All `/learn/...` endpoints require a verified-email JWT. `GET` endpoints accept either an enrolled learner or the course's own instructor (preview). `POST /progress/` is learner-only.

### 12B.1 Get Learner Curriculum Outline
- Method: `GET`
- URL: `{{base_url}}/learn/{{course_slug}}/curriculum/`
- Headers: enrolled learner OR course's instructor JWT
- Expected status: `200`
- Expected response shape:
```json
{
  "success": true,
  "data": {
    "course": {
      "id": 101,
      "slug": "python-backend-bootcamp",
      "title": "Python Backend Bootcamp"
    },
    "sections": [
      {
        "id": 11,
        "title": "Getting Started",
        "position": 1,
        "items": [
          {
            "content_id": 201,
            "object_id": 301,
            "item_type": "lecture",
            "position": 1,
            "title": "Welcome",
            "lecture_type": "article",
            "duration_seconds": null,
            "is_completed": false
          },
          {
            "content_id": 202,
            "object_id": 302,
            "item_type": "lecture",
            "position": 2,
            "title": "Intro Video",
            "lecture_type": "video",
            "duration_seconds": 600,
            "is_completed": true
          },
          {
            "content_id": 203,
            "object_id": 50,
            "item_type": "quiz",
            "position": 3,
            "title": "Intro Quiz"
          },
          {
            "content_id": 204,
            "object_id": 1,
            "item_type": "coding",
            "position": 4,
            "title": "Reverse a String",
            "difficulty": "easy"
          }
        ]
      }
    ]
  }
}
```
- Notes:
  - `is_completed` appears only for learners; instructors previewing the curriculum get the same payload without that key.
  - Heavy item payloads (HLS URLs, quiz questions, article text, coding configs) are not in this response — fetch them from per-item endpoints.

### 12B.2 Learner Curriculum Error Cases
**Unenrolled learner**
- Authenticated learner with no `Enrollment` for the course.
- Expected status: `403`
- Example response:
```json
{
  "success": false,
  "message": "You do not have access to this course."
}
```

**Course not found**
- Slug does not match any course.
- Expected status: `404`

**Unauthenticated**
- Omit the `Authorization` header.
- Expected status: `401`

### 12B.3 Get Learner Lecture Detail
- Method: `GET`
- URL: `{{base_url}}/learn/lectures/{{lecture_id}}/`
- Headers: enrolled learner OR course's instructor JWT
- Expected status: `200`
- Expected response (video lecture, learner caller):
```json
{
  "success": true,
  "data": {
    "id": 302,
    "section_id": 11,
    "title": "Intro Video",
    "lecture_type": "video",
    "article_content": "",
    "stream_master_playlist": "courses/python-backend-bootcamp/lectures/302/hls/.../master.m3u8",
    "stream_renditions": [
      { "label": "720p", "playlist": "courses/.../720p/playlist.m3u8" }
    ],
    "duration_seconds": 600,
    "progress": {
      "watched_seconds": 120,
      "is_completed": false,
      "last_watched_at": "2026-05-17T09:14:22Z"
    }
  }
}
```
- Expected response (article lecture):
```json
{
  "success": true,
  "data": {
    "id": 301,
    "section_id": 11,
    "title": "Welcome",
    "lecture_type": "article",
    "article_content": "HTTP methods, status codes, and API design basics.",
    "stream_master_playlist": "",
    "stream_renditions": [],
    "duration_seconds": null,
    "progress": { "watched_seconds": 0, "is_completed": true, "last_watched_at": "..." }
  }
}
```
- Notes:
  - `progress` is `null` for the instructor preview caller (no per-instructor watch history is tracked).
  - `transcoding_error` is intentionally not exposed.

### 12B.4 Learner Lecture Detail Error Cases
**Unenrolled learner**
- Expected status: `404` (existence is not leaked).
- Example response:
```json
{ "success": false, "message": "Lecture not found." }
```

**Lecture not found**
- Expected status: `404`.

### 12B.5 Upsert Watch Progress
- Method: `POST`
- URL: `{{base_url}}/learn/lectures/{{lecture_id}}/progress/`
- Headers: enrolled learner JWT (instructors get `403`)
- Body:
```json
{
  "watched_seconds": 120,
  "is_completed": false
}
```
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "message": "Progress saved.",
  "data": {
    "lecture_id": 302,
    "watched_seconds": 120,
    "is_completed": false,
    "last_watched_at": "2026-05-17T09:14:22Z"
  }
}
```
- Notes:
  - Idempotent — repeated POSTs with the same body never create duplicate `WatchProgress` rows.
  - `watched_seconds` is server-clamped to the active video's `duration_seconds`. Sending `99999` for a 600-second video stores `600`, not `99999`.
  - If the clamped cursor lands at duration, the server forces `is_completed: true` regardless of what the client sent — reaching the end of the file *is* completion. The response body reflects the corrected values.
  - Article lectures have no duration; `watched_seconds` is forced to `0` on save.
  - When `is_completed` flips, a signal recalculates the enrollment's `progress_percent`. Re-fetch `/my-courses/` to see the updated rollup.

### 12B.6 Progress Endpoint Error Cases
**Negative `watched_seconds`**
- Body: `{ "watched_seconds": -5, "is_completed": false }`
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": { "watched_seconds": ["Ensure this value is greater than or equal to 0."] }
}
```

**Missing `is_completed`**
- Body: `{ "watched_seconds": 30 }`
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": { "is_completed": ["This field is required."] }
}
```

**Unenrolled learner**
- Authenticated learner with no enrollment for the lecture's course.
- Expected status: `404` (existence not leaked).

**Instructor calling the progress endpoint**
- Authenticated as the course's instructor.
- Expected status: `403` (`Only learners can access this resource.`). Instructor preview is read-only.

### 12B.7 Get Learner Quiz Detail (Attempt UI)
- Method: `GET`
- URL: `{{base_url}}/learn/quizzes/{{quiz_id}}/`
- Headers: enrolled learner OR course's instructor JWT
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "data": {
    "id": 50,
    "section_id": 11,
    "title": "REST Basics Quiz",
    "description": "Checks understanding of HTTP and endpoints.",
    "question_count": 3,
    "questions": [
      {
        "id": 1,
        "question_text": "Which HTTP method is idempotent?",
        "position": 1,
        "answers": [
          { "id": 5, "answer_text": "POST" },
          { "id": 6, "answer_text": "PUT" },
          { "id": 7, "answer_text": "PATCH" }
        ]
      }
    ],
    "latest_attempt": {
      "attempt_id": 12,
      "score": 2,
      "max_score": 3,
      "submitted_at": "2026-05-17T09:14:22Z"
    }
  }
}
```
- Notes:
  - Each answer option carries only `id` + `answer_text` — `is_correct` is **never** in this payload.
  - `latest_attempt` is `null` if the caller has never submitted (or is an instructor previewing).
  - Instructor preview is allowed and gets the same shape (no `is_correct` leak even to them — the safer default).

### 12B.8 Submit a Quiz Attempt
- Method: `POST`
- URL: `{{base_url}}/learn/quizzes/{{quiz_id}}/submit/`
- Headers: enrolled learner JWT (instructors get `403`)
- Body — list every question with its selected answer (or `null` to leave it unanswered):
```json
{
  "answers": [
    { "question_id": 1, "selected_answer_id": 6 },
    { "question_id": 2, "selected_answer_id": 9 },
    { "question_id": 3, "selected_answer_id": null }
  ]
}
```
- Expected status: `200`
- Expected response — score + per-question verdict. `correct_answer_id` / `correct_answer_text` appear **only when `is_correct=false`**:
```json
{
  "success": true,
  "message": "Quiz submitted.",
  "data": {
    "attempt_id": 13,
    "score": 1,
    "max_score": 3,
    "submitted_at": "2026-05-17T09:32:08Z",
    "questions": [
      {
        "question_id": 1,
        "question_text": "Which HTTP method is idempotent?",
        "selected_answer_id": 6,
        "selected_answer_text": "PUT",
        "is_correct": true
      },
      {
        "question_id": 2,
        "question_text": "Which status code means \"Created\"?",
        "selected_answer_id": 9,
        "selected_answer_text": "204",
        "is_correct": false,
        "correct_answer_id": 11,
        "correct_answer_text": "201"
      },
      {
        "question_id": 3,
        "question_text": "Which header carries the bearer token?",
        "selected_answer_id": null,
        "selected_answer_text": null,
        "is_correct": false,
        "correct_answer_id": 14,
        "correct_answer_text": "Authorization"
      }
    ]
  }
}
```
- Notes:
  - Each POST creates a **new** `QuizAttempt` row — repeated submits don't overwrite past attempts.
  - Unanswered questions (`selected_answer_id: null`) score as wrong and reveal the correct answer.
  - Each successful submit recalculates `enrollment.progress_percent` (a quiz counts as complete once the learner has ≥1 `QuizAttempt` row for it). Re-fetch `/my-courses/{{course_slug}}/` to see the updated rollup.

### 12B.9 Quiz Submission Error Cases

**Question ID not in this quiz**
- Body references a `question_id` from a different quiz.
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "answers": ["question_id 99 does not belong to this quiz."]
  }
}
```

**Answer ID not under the cited question**
- `selected_answer_id` belongs to a different question.
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "answers": ["selected_answer_id 22 does not belong to question 1."]
  }
}
```

**Duplicate question_id in payload**
- Same `question_id` appears more than once.
- Expected status: `400`

**Unenrolled learner**
- Authenticated learner with no enrollment for the quiz's course.
- Expected status: `404` (existence not leaked).

**Instructor calling submit**
- Authenticated as the course's instructor.
- Expected status: `403`. Instructor preview is read-only — attempt history stays clean.

### 12B.10 Get Learner Assignment Detail (Attempt UI)
- Method: `GET`
- URL: `{{base_url}}/learn/assignments/{{assignment_id}}/`
- Headers: enrolled learner JWT (instructor-preview also allowed).
- Expected status: `200`
- Example response:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "section_id": 1,
    "title": "REST Reflection",
    "description": "Reflect on what you learned.",
    "instructions": "Answer both questions fully.",
    "passing_score": 5,
    "max_score": 10,
    "question_count": 2,
    "questions": [
      {
        "id": 1,
        "question_text": "What surprised you most about REST design?",
        "points": 6,
        "hint": "Reference at least one HTTP verb.",
        "position": 1
      },
      {
        "id": 2,
        "question_text": "How does idempotency change retry logic?",
        "points": 4,
        "hint": "",
        "position": 2
      }
    ],
    "latest_submission": null
  }
}
```

Notes:
- `model_answer` and `rubric` are **never** present in this payload. The learner serializer doesn't declare them; absence is a stronger guarantee than conditional removal.
- `latest_submission` summarizes the caller's most recent submission (`submission_id`, `status`, `total_score`, `max_score`, `submitted_at`, `graded_at`). Use it to decide whether to show the new-attempt form or surface the prior submission's feedback (see *Resubmission UX* below).

### 12B.11 Submit an Assignment (auto-graded)
- Method: `POST`
- URL: `{{base_url}}/learn/assignments/{{assignment_id}}/submit/`
- Headers: enrolled learner JWT.
- Body:
```json
{
  "answers": [
    {"question_id": 1, "answer_text": "Idempotency means PUT and DELETE are safe to retry without compounding side effects, unlike POST. That guides retry policy at the gateway."},
    {"question_id": 2, "answer_text": "Idempotent verbs let the client retry on network failure without worrying about duplicate state changes."}
  ]
}
```
- Expected status: `202 Accepted`
- Example response:
```json
{
  "success": true,
  "message": "Assignment submitted. Grading is in progress.",
  "data": {
    "submission_id": 7,
    "assignment_id": 1,
    "status": "submitted",
    "submitted_at": "2026-05-20T11:42:18.301Z",
    "max_score": 10
  }
}
```

Notes:
- The response returns immediately with `status='submitted'`. A Celery task (`grade_assignment_submission_task`) runs the rubric grader out-of-band. The learner should poll `GET /learn/assignments/submissions/{submission_id}/` until `status` transitions to a terminal value (`passed`, `failed`, or `grading_failed`).
- `max_score` is snapshotted at submit time. Even if the instructor later edits `AssignmentQuestion.points`, the submission's max stays frozen — historical submissions never get retroactively rescored.
- All questions on the assignment must appear in the `answers` array (use an empty string `""` for a deliberately-blank answer). Missing a question → `400`.

### 12B.12 Get Learner Submission Detail (polling target)
- Method: `GET`
- URL: `{{base_url}}/learn/assignments/submissions/{{submission_id}}/`
- Headers: the same learner that submitted (other learners → `404`).
- Expected status: `200`
- Example response while still grading:
```json
{
  "success": true,
  "data": {
    "submission_id": 7,
    "assignment_id": 1,
    "status": "grading",
    "total_score": 0,
    "max_score": 10,
    "submitted_at": "2026-05-20T11:42:18.301Z",
    "graded_at": null,
    "grading_error": "",
    "answers": [
      {
        "question_id": 1,
        "question_text": "What surprised you most about REST design?",
        "answer_text": "Idempotency means PUT and DELETE are safe to retry ...",
        "score": 0,
        "max_score": 6,
        "criterion_results": [],
        "feedback": ""
      }
    ]
  }
}
```
- Example response once graded (`status='passed'` or `'failed'`):
```json
{
  "success": true,
  "data": {
    "submission_id": 7,
    "assignment_id": 1,
    "status": "passed",
    "total_score": 10,
    "max_score": 10,
    "submitted_at": "2026-05-20T11:42:18.301Z",
    "graded_at": "2026-05-20T11:42:19.522Z",
    "grading_error": "",
    "answers": [
      {
        "question_id": 1,
        "question_text": "What surprised you most about REST design?",
        "answer_text": "Idempotency means PUT and DELETE are safe to retry ...",
        "score": 6,
        "max_score": 6,
        "criterion_results": [
          {"index": 0, "type": "keyword",    "matched": true,  "points_awarded": 4, "feedback": "Correctly identifies idempotency."},
          {"index": 1, "type": "any_of",     "matched": true,  "points_awarded": 2, "feedback": "Mentions an HTTP verb."}
        ],
        "feedback": "Correctly identifies idempotency.\nMentions an HTTP verb.",
        "model_answer": "Reference reflection: idempotency boundaries, ..."
      },
      {
        "question_id": 2,
        "question_text": "How does idempotency change retry logic?",
        "answer_text": "Idempotent verbs let the client retry ...",
        "score": 4,
        "max_score": 4,
        "criterion_results": [
          {"index": 0, "type": "keyword", "matched": true, "points_awarded": 4, "feedback": "Mentions retry logic."}
        ],
        "feedback": "Mentions retry logic.",
        "model_answer": "An idempotent verb means clients can safely retry ..."
      }
    ]
  }
}
```

Reveal rule (verify in tests):
- `model_answer` is **omitted entirely** on each answer when `status in ('submitted', 'grading', 'grading_failed')`. It is **included** only when `status in ('passed', 'failed')`.

**Polling pattern (frontend reference):**

1. After `POST /submit/` returns `202`, poll `GET /learn/assignments/submissions/{submission_id}/` every 2–5 seconds.
2. Stop polling once `status` is one of `passed`, `failed`, or `grading_failed`.
3. If terminal status is `grading_failed`, show the `grading_error` to the user and offer the retry button (see 12B.13).

### 12B.13 Retry a Failed Grading
- Method: `POST`
- URL: `{{base_url}}/learn/assignments/submissions/{{submission_id}}/retry/`
- Headers: the same learner that owns the submission.
- Body: empty (none required).
- Expected status: `202 Accepted` when the prior status was `grading_failed`.
- Example response:
```json
{
  "success": true,
  "message": "Grading re-enqueued.",
  "data": {
    "submission_id": 7,
    "status": "grading"
  }
}
```

Notes:
- The same submission row is reused — `submitted_at` is unchanged, `grading_error` is cleared, `status` flips to `grading`, and the Celery task is re-dispatched. The learner can keep polling the same submission detail endpoint.
- Only `grading_failed` is retryable. Any other status → `422` with `"Only submissions in grading_failed can be retried."`.
- A submission owned by a different learner → `404` (existence not leaked).
- For a learner who wants to take a fresh attempt after a graded `failed`/`passed`, use `POST /submit/` to create a new submission row — that's distinct from `/retry/`, which only re-runs the grader against the existing answers.

### 12B.14 Assignment Submission Error Cases

**In-flight submission already exists** (caller has one with `status in ('submitted', 'grading')`).
- Expected status: `422`
- Example response:
```json
{
  "success": false,
  "message": "You already have a submission for this assignment that is still being graded."
}
```

**Question ID not in this assignment.**
- Expected status: `400` with `errors.answers` listing the offending IDs.

**Duplicate `question_id` in the payload.**
- Expected status: `400`.

**Missing some questions in the payload.**
- Expected status: `400`. The serializer enforces all-or-nothing — every question on the assignment must appear in `answers`. Use `"answer_text": ""` for an intentionally-blank answer.

**Unenrolled learner.**
- Expected status: `404` (existence not leaked — same as a non-existent assignment).

**Instructor calling `/submit/` or `/retry/`.**
- Expected status: `403`. Preview must not pollute submission history.

**Submission detail for another learner's submission.**
- Expected status: `404`.

### Resubmission UX (frontend responsibility)

After a `failed` (or `grading_failed`) verdict the learner can retry the grader (12B.13) **or** submit fresh answers via `POST /submit/`. The frontend should fetch the most recent submission detail and render its `criterion_results` next to the new submission form — otherwise the learner has no idea which criteria they missed last time. The backend already returns everything needed; this is a UI wiring concern.

## 12C) My-Courses Endpoints (course header + dashboard)

The `/my-courses/` family is for the learner's dashboard and the course-player page header. The course-player UI composes its full page from three calls: `/my-courses/<slug>/` (header card with metadata + overall progress), `/learn/<slug>/curriculum/` (sidebar), and `/learn/<thing>/<id>/` (the item the learner clicked).

### 12C.1 List My Enrollments (Dashboard)
- Method: `GET`
- URL: `{{base_url}}/my-courses/`
- Headers: enrolled learner JWT
- Expected status: `200`
- Expected response (paginated):
```json
{
  "success": true,
  "data": {
    "count": 2,
    "next": null,
    "previous": null,
    "results": [
      {
        "id": 11,
        "course": {
          "id": 101, "title": "Python Backend Bootcamp", "slug": "python-backend-bootcamp",
          "description": "...", "thumbnail": null, "price": "79.99",
          "language": "English", "level": "intermediate", "duration_minutes": 240,
          "instructors": [...], "category": {...}, "published_at": "..."
        },
        "enrollment_type": "free",
        "is_active": true,
        "progress_percent": 35,
        "completed_at": null,
        "last_accessed_at": "2026-05-17T09:14:22Z",
        "created_at": "2026-05-01T08:00:00Z"
      }
    ]
  }
}
```
- Ordered by `last_accessed_at` (most recent first), then `created_at`. Only the caller's own active enrollments are returned.

### 12C.2 Get My-Course Detail (Player Header)
- Method: `GET`
- URL: `{{base_url}}/my-courses/{{course_slug}}/`
- Headers: enrolled learner OR course's instructor JWT
- Expected status: `200`
- Expected response:
```json
{
  "success": true,
  "data": {
    "is_instructor": false,
    "enrollment": {
      "id": 11,
      "enrollment_type": "free",
      "is_active": true,
      "progress_percent": 35,
      "completed_at": null,
      "last_accessed_at": "2026-05-17T09:14:22Z",
      "created_at": "2026-05-01T08:00:00Z"
    },
    "course": {
      "id": 101,
      "title": "Python Backend Bootcamp",
      "slug": "python-backend-bootcamp",
      "description": "Build production APIs with Django and DRF.",
      "thumbnail": null,
      "price": "79.99",
      "language": "English",
      "level": "intermediate",
      "duration_minutes": 240,
      "status": "published",
      "is_published": true,
      "published_at": "2026-04-22T11:00:00Z",
      "instructors": [...],
      "partner_institutions": [],
      "category": {...},
      "learning_objectives": [...],
      "prerequisites": [...],
      "audiences": [...],
      "total_sections": 12,
      "total_content_items": 47
    }
  }
}
```
- Notes:
  - This response does **not** include the curriculum tree. Fetch `/learn/{{course_slug}}/curriculum/` for the sidebar.
  - `is_instructor: true` is returned when the caller is one of the course's instructors (preview mode). In that case `enrollment` is `null`.
  - Each GET call updates the learner's `last_accessed_at` timestamp on the enrollment row.

### 12C.3 My-Courses Detail Error Cases

**Unenrolled learner**
- Authenticated learner with no enrollment for the course.
- Expected status: `403`
- Example response:
```json
{
  "success": false,
  "message": "You do not have access to this course."
}
```

**Course not found**
- Slug does not match any course.
- Expected status: `404`

**Unauthenticated**
- Omit the `Authorization` header.
- Expected status: `401`

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
