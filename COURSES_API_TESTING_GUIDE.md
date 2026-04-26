# Courses API Testing Guide (Django + DRF + Celery + FFmpeg)

## Base URLs
- `http://127.0.0.1:8000/api/v1/courses`
- Auth endpoints (to get token): `http://127.0.0.1:8000/api/v1/auth`

## Prerequisites
1. Install dependencies:
```bash
pip install -r requirements.txt
```
2. Ensure `.env` has:
```env
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
MEDIA_URL=/media/
MEDIA_ROOT=<absolute-path-to-project>/media
FFMPEG_BINARY_PATH=<absolute-path-to-ffmpeg.exe-or-ffmpeg>
FFPROBE_BINARY_PATH=<absolute-path-to-ffprobe.exe-or-ffprobe>  # optional but recommended
```
3. Start Redis.
4. Start Django server:
```bash
python manage.py runserver
```
5. Start Celery worker:
```bash
celery -A career_college_backend worker -l info
```
6. Login as instructor and get JWT access token.

## Auth Header
Use this on all Courses requests:
```http
Authorization: Bearer <access_token>
```

---

## 1. Course Endpoints

### 1.1 List Courses
- **GET** `/api/v1/courses/`
- Route name: `courses:course-list`

### 1.2 Create Course
- **POST** `/api/v1/courses/create/`
- Route name: `courses:course-create`
- JSON body:
```json
{
  "title": "Django Backend Masterclass",
  "description": "Production-grade backend design",
  "price": "49.99",
  "language": "English",
  "level": "intermediate",
  "duration_minutes": 180,
  "category": 1
}
```
- Save returned `id` as `course_id`.

Note:
- `category` expects an active `CourseCategory` primary key.
- You can verify category IDs from Django admin or shell.

### 1.3 Course Detail / Update
- **GET** `/api/v1/courses/{course_id}/`
- **PATCH** `/api/v1/courses/{course_id}/`
- Example PATCH:
```json
{
  "status": "under_review"
}
```

---

## 2. Course Section Endpoints

### 2.1 List Sections by Course
- **GET** `/api/v1/courses/{course_id}/sections/`
- Optional ordering query:
  - `?ordering=position`
  - `?ordering=-position`

### 2.2 Create Section
- **POST** `/api/v1/courses/{course_id}/sections/create/`
- JSON body:
```json
{
  "title": "Getting Started",
  "description": "Intro section",
  "position": 1
}
```
- Save returned `id` as `section_id`.

### 2.3 Section Detail / Update / Delete
- **GET** `/api/v1/courses/sections/{section_id}/`
- **PATCH** `/api/v1/courses/sections/{section_id}/`
- **PUT** `/api/v1/courses/sections/{section_id}/`
- **DELETE** `/api/v1/courses/sections/{section_id}/`

---

## 3. Lecture Endpoints

## 3.1 List Lectures by Section
- **GET** `/api/v1/courses/sections/{section_id}/lectures/`
- Optional ordering query:
  - `?ordering=position`
  - `?ordering=-position`

### 3.2 Create Article Lecture
- **POST** `/api/v1/courses/sections/{section_id}/lectures/create/`
- `Content-Type: application/json`
```json
{
  "title": "Architecture Overview",
  "position": 1,
  "content_type": "article",
  "article_content": "This is the rich text body..."
}
```

### 3.3 Create Video Lecture (Upload)
- **POST** `/api/v1/courses/sections/{section_id}/lectures/create/`
- `Content-Type: multipart/form-data`
- Form fields:
  - `title`: `Intro Video`
  - `position`: `2`
  - `content_type`: `video`
  - `video_file`: *(choose file)*

Expected:
1. Lecture is created.
2. `active_video_asset.status` starts as `processing`.
3. Celery task is queued automatically.

### 3.4 Lecture Detail / Update / Delete
- **GET** `/api/v1/courses/lectures/{lecture_id}/`
- **PATCH** `/api/v1/courses/lectures/{lecture_id}/`
- **PUT** `/api/v1/courses/lectures/{lecture_id}/`
- **DELETE** `/api/v1/courses/lectures/{lecture_id}/`

---

## 4. Video Transcoding Verification Flow

After creating a video lecture:

1. Call lecture detail:
   - `GET /api/v1/courses/lectures/{lecture_id}/`
2. Check response fields:
   - `active_video_asset.status`
   - `stream_master_playlist`
   - `stream_renditions`
   - `transcoding_error`

Expected progression:
1. `active_video_asset.status = "processing"` initially
2. After worker completes:
   - `active_video_asset.status = "ready"`
   - `stream_master_playlist` populated
   - `stream_renditions` contains 5 entries: `240p`, `360p`, `480p`, `720p`, `1080p`
   - each rendition has concrete resolution like `426x240`, `640x360`, `854x480`, `1280x720`, `1920x1080` (varies by source)

If failure:
1. `active_video_asset.status = "failed"`
2. `transcoding_error` contains error text
3. Check Celery worker logs for FFmpeg command failure details.
4. If resolutions look like `0x720`, ffprobe likely failed or is missing; set `FFPROBE_BINARY_PATH`.

---

## 5. Validation Cases to Test

### Lecture content-type rules
1. `article` lecture with `video_file` -> should fail `400`.
2. `article` lecture without `article_content` -> should fail `400`.
3. `video` lecture with non-empty `article_content` -> should fail `400`.
4. `video` lecture create without `video_file` -> should fail `400`.

### Ownership / access rules
1. Access course not assigned to instructor -> should return `404`.
2. Access section/lecture outside instructor’s course ownership -> should return `404`.

---

## 6. Quick End-to-End Test Script (Manual)

1. Login as verified instructor and copy access token.
2. Create course -> get `course_id`.
3. Create section -> get `section_id`.
4. Create article lecture -> verify in lecture list.
5. Create video lecture with file upload -> get `lecture_id`.
6. Poll `GET /lectures/{lecture_id}/` every 5–10 seconds.
7. Confirm video status transitions to `ready` and HLS fields are saved.

---

## 7. Common Issues

1. `403` on course create/list:
   - Instructor may not be verified (`IsVerifiedInstructor` required).
2. `ffmpeg not recognized`:
   - Set `FFMPEG_BINARY_PATH` to full binary path.
3. `ffprobe not recognized` or odd resolution metadata:
   - Set `FFPROBE_BINARY_PATH` to full binary path.
4. Task not running:
   - Celery worker not started or broker unavailable.
5. Stuck in `processing`:
   - Inspect worker logs, Redis health, and FFmpeg install/path.

---

## 8. Optional Postman Environment Variables

```text
courses_base_url = http://127.0.0.1:8000/api/v1/courses
access_token = <jwt-access-token>
course_id = <created-course-id>
section_id = <created-section-id>
lecture_id = <created-lecture-id>
```
