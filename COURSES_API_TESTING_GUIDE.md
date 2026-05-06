# Courses and Quizzes API Testing Guide (Postman)

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
  "status": "under_review"
}
```
- Expected status: `200`

## 6) Section API Flow

### 6.1 Create Section
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

### 6.2 List Sections
- Method: `GET`
- URL: `{{base_url}}/{{course_id}}/sections/`
- Optional query params:
  - `ordering=position`
  - `ordering=-position`

### 6.3 Get / Update / Delete Section
- `GET {{base_url}}/sections/{{section_id}}/`
- `PATCH {{base_url}}/sections/{{section_id}}/`
- `PUT {{base_url}}/sections/{{section_id}}/`
- `DELETE {{base_url}}/sections/{{section_id}}/`

## 7) Section Content API (the only creation path for all content)

All lectures, quizzes, and coding exercises must be created through this endpoint. There are no separate creation endpoints for individual content types.

### 7.1 Create Article Lecture
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Body:
```json
{
  "item_type": "lecture",
  "title": "REST Fundamentals",
  "content_type": "article",
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
      "content_type": "article"
    }
  }
}
```
- Save `data.object_id` as `lecture_id`.

### 7.1b Create Video Lecture (multipart/form-data)
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Body type: `form-data`
  - `item_type` = `lecture`
  - `title` = `Intro Video`
  - `content_type` = `video`
  - `video_file` = (select file)
  - `position` = `2` (optional)
- Expected status: `201`
- After creation the video is queued for transcoding. Poll `GET {{base_url}}/lectures/{{lecture_id}}/` until `active_video_asset.status` is `ready` or `failed`.

### 7.2 Create Quiz via Section Content
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

### 7.2b Create Coding Exercise via Section Content
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

### 7.3 List Ordered Curriculum
- Method: `GET`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Expected status: `200`
- Returns all content items (lectures, quizzes, coding exercises) ordered by `position`.

### 7.4 Reorder Curriculum Item
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

## 8) Lecture Endpoints (read / update / delete)

Create lectures via `sections/{id}/contents/` (section 7). These endpoints are for reading and modifying existing lectures.

### 8.1 List Lectures in a Section
- `GET {{base_url}}/sections/{{section_id}}/lectures/`

### 8.2 Get / Patch / Put / Delete Lecture
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

## 9) Quiz API Flow

Create quizzes via `sections/{id}/contents/` (section 7.2). These endpoints are for reading and modifying existing quizzes and their questions/answers.

### 9.1 Get / Patch / Delete Quiz
- `GET {{base_url}}/quizzes/{{quiz_id}}/`
- `PATCH {{base_url}}/quizzes/{{quiz_id}}/`
  - Demo patch body:
```json
{
  "title": "Django ORM Quiz (Updated)"
}
```
- `DELETE {{base_url}}/quizzes/{{quiz_id}}/`

### 9.2 Create Quiz Question
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

### 9.3 List Quiz Questions
- Method: `GET`
- URL: `{{base_url}}/quizzes/{{quiz_id}}/questions/`

### 9.4 Update/Delete Question
- `PATCH {{base_url}}/quiz-questions/{{question_id}}/`
- `DELETE {{base_url}}/quiz-questions/{{question_id}}/`

### 9.5 Create Quiz Answers
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

### 9.6 List / Update / Delete Answers
- `GET {{base_url}}/quiz-questions/{{question_id}}/answers/`
- `PATCH {{base_url}}/quiz-answers/{{answer_id}}/`
- `DELETE {{base_url}}/quiz-answers/{{answer_id}}/`

## 10) Coding Exercise API Flow

Create coding exercises via `sections/{id}/contents/` (section 7.2b). All endpoints below require a verified instructor JWT. The exercise must belong to a section of a course you instruct — otherwise the API returns `404`.

### 10.1 Get Coding Exercise Detail
- Method: `GET`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/`
- Expected status: `200`

### 10.2 Patch Coding Exercise
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

### 10.3 Delete Coding Exercise
- Method: `DELETE`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/`
- Expected status: `204`
- Expected behavior: deletes the exercise and its `SectionContent` slot automatically (cascade via `GenericRelation`).

---

### 10.4 Add Language Config
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

### 10.5 Duplicate Language Config (error case)
- Repeat the same POST above with `"language": "python"`.
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "A config for this language already exists on this exercise."
}
```

### 10.6 List Language Configs
- Method: `GET`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/language-configs/`
- Expected status: `200`

### 10.7 Get / Patch / Delete Language Config
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

### 10.8 Add Test Case
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

### 10.9 Duplicate Test Case Position (error case)
- Repeat POST with `"position": 1` for the same exercise.
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "A test case already exists at that position for this exercise."
}
```

### 10.10 List Test Cases
- Method: `GET`
- URL: `{{base_url}}/coding-exercises/{{exercise_id}}/testcases/`
- Expected status: `200`
- Results are ordered by `position`.

### 10.11 Get / Patch / Delete Test Case
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

### 10.12 Coding Exercise Validation Error Cases

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

## 11) Common Error Responses You Should Test

### 11.1 Invalid `item_type` in section contents
- Request:
```json
{
  "item_type": "assignment",
  "title": "Invalid Type"
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "item_type must be 'lecture', 'quiz', or 'coding'."
}
```

### 11.2 Invalid reorder position
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

### 11.3 Two correct answers for one question
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

## 12) Quick Manual End-to-End Scenario
1. Create course → save `course_id`.
2. Create section → save `section_id`.
3. Create one article lecture via `sections/{id}/contents/` (`item_type: "lecture"`).
4. Create one quiz via `sections/{id}/contents/` (`item_type: "quiz"`) → save `quiz_id`.
5. Create one coding exercise via `sections/{id}/contents/` (`item_type: "coding"`) → save `exercise_id`.
6. List `sections/{id}/contents/` — verify all three items appear with correct `content` summaries.
7. Reorder coding exercise to position `1`; verify list updates with shifted items.
8. Add a Python language config to the exercise (`POST coding-exercises/{id}/language-configs/`).
9. Add two test cases (one visible, one hidden) to the exercise.
10. `GET coding-exercises/{id}/` — verify `language_configs` and `test_cases` arrays are populated.
11. Add one question and two answers (one correct) to quiz.
12. Patch exercise difficulty to `"hard"` and verify update.
13. Delete the exercise — re-fetch `sections/{id}/contents/` and confirm the coding slot is gone.

## 13) Notes
- All ownership checks are instructor-scoped; if the course/section/exercise is not yours, API returns `404`.
- `solution_code` on language configs is stored server-side and must never appear in learner-facing responses (Part 2 enforcement).
- Hidden test cases (`is_hidden: true`) are for grading only and must never be exposed to learners (Part 2 enforcement).
- For video lectures, transcoding states usually move from `processing` to `ready` (or `failed` on error).
