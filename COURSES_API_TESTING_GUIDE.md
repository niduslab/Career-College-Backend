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

## 7) Section Content API (recommended curriculum endpoints)

These endpoints let you mix lectures and quizzes in one ordered list.

### 7.1 Create Lecture via Section Content (JSON article)
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

### 7.3 List Ordered Curriculum
- Method: `GET`
- URL: `{{base_url}}/sections/{{section_id}}/contents/`
- Expected status: `200`
- You should see mixed ordered rows for lectures and quizzes.

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

## 8) Legacy Lecture Endpoints (still available)

### 8.1 Create Article Lecture
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/lectures/create/`
- Body:
```json
{
  "title": "Database Modeling",
  "content_type": "article",
  "article_content": "Normalization, indexes, and relations."
}
```
- Expected status: `201`

### 8.2 Create Video Lecture (multipart/form-data)
- Method: `POST`
- URL: `{{base_url}}/sections/{{section_id}}/lectures/create/`
- Body type: `form-data`
  - `title` = `Intro Video`
  - `content_type` = `video`
  - `video_file` = (select file)
  - Optional `position` = `3`
- Expected status: `201`
- Check processing via `GET {{base_url}}/lectures/{{lecture_id}}/`.

### 8.3 Lecture Read / Update / Delete
- `GET {{base_url}}/sections/{{section_id}}/lectures/`
- `GET {{base_url}}/lectures/{{lecture_id}}/`
- `PATCH {{base_url}}/lectures/{{lecture_id}}/`
- `PUT {{base_url}}/lectures/{{lecture_id}}/`
- `DELETE {{base_url}}/lectures/{{lecture_id}}/`

## 9) Quiz API Flow (direct quiz endpoints)

### 9.1 Create Quiz (direct endpoint)
- Method: `POST`
- URL: `{{base_url}}/quizzes/`
- Body:
```json
{
  "section": {{section_id}},
  "title": "Django ORM Quiz",
  "description": "Model querying and relations",
  "position": 3
}
```
- Expected status: `201`
- Save `data.id` as `quiz_id`.

### 9.2 Get / Patch / Delete Quiz
- `GET {{base_url}}/quizzes/{{quiz_id}}/`
- `PATCH {{base_url}}/quizzes/{{quiz_id}}/`
  - Demo patch body:
```json
{
  "title": "Django ORM Quiz (Updated)"
}
```
- `DELETE {{base_url}}/quizzes/{{quiz_id}}/`

### 9.3 Create Quiz Question
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

### 9.4 List Quiz Questions
- Method: `GET`
- URL: `{{base_url}}/quizzes/{{quiz_id}}/questions/`

### 9.5 Update/Delete Question
- `PATCH {{base_url}}/quiz-questions/{{question_id}}/`
- `DELETE {{base_url}}/quiz-questions/{{question_id}}/`

### 9.6 Create Quiz Answers
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

### 9.7 List / Update / Delete Answers
- `GET {{base_url}}/quiz-questions/{{question_id}}/answers/`
- `PATCH {{base_url}}/quiz-answers/{{answer_id}}/`
- `DELETE {{base_url}}/quiz-answers/{{answer_id}}/`

## 10) Common Error Responses You Should Test

### 10.1 Missing section on direct quiz create
- Request:
```json
{
  "title": "Quiz Without Section",
  "description": "No section supplied"
}
```
- Expected status: `400`
- Example response:
```json
{
  "success": false,
  "message": "Validation failed.",
  "errors": {
    "section": [
      "Section is required."
    ]
  }
}
```

### 10.2 Invalid `item_type` in section contents
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
  "message": "item_type must be 'lecture' or 'quiz'."
}
```

### 10.3 Invalid reorder position
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

### 10.4 Two correct answers for one question
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

## 11) Quick Manual End-to-End Scenario
1. Create course.
2. Create section.
3. Create one article lecture via `sections/{id}/contents/`.
4. Create one quiz via `sections/{id}/contents/`.
5. List `sections/{id}/contents/` and verify order.
6. Reorder quiz to position `1`; verify list updates with shifted items.
7. Add one question and two answers (one correct) to quiz.
8. Patch quiz title and verify update works.

## 12) Notes
- All ownership checks are instructor-scoped; if the course/section/quiz is not yours, API returns `404`.
- For video lectures, transcoding states usually move from `processing` to `ready` (or `failed` on error).
