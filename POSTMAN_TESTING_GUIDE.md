# Postman Testing Guide — Career College Backend

A single combined guide covering Authentication, Profiles, ID Verification,
Course Authoring, Public Catalog, Enrollment, the Learner Dashboard, and
Learner Consumption endpoints.

## Table of Contents

### Setup
- [1. Base URL](#1-base-url)
- [2. Prerequisites](#2-prerequisites)
- [3. Postman Environment Variables](#3-postman-environment-variables)
- [4. Required Headers](#4-required-headers)

### Authentication
- [5. Register — Learner / Instructor / Partner Institution](#5-register)
- [6. Register — Validation Error Cases](#6-register--validation-error-cases)
- [7. Verify OTP](#7-verify-otp)
- [8. Resend OTP](#8-resend-otp)
- [9. Login](#9-login)
- [10. Refresh Token](#10-refresh-token)
- [11. Logout](#11-logout)
- [12. Google Sign-In](#12-google-sign-in)

### Password Management
- [13. Forgot Password](#13-forgot-password)
- [14. Verify OTP for Password Reset](#14-verify-otp-for-password-reset)
- [15. Reset Password](#15-reset-password)
- [15A. Change Password (Authenticated User)](#15a-change-password-authenticated-user)

### Profile Management
- [16. My Profile — Get / Update / Photo](#16-my-profile)
- [17. Education](#17-education)
- [18. Work Experience](#18-work-experience)

### Public Profiles
- [19. Public Profile by Slug](#19-public-profile-by-slug)
- [20. Browse Public Profile Lists](#20-browse-public-profile-lists)

### Instructor ID Verification
- [21. Create Draft Verification](#21-create-draft-verification)
- [22. Update Draft Verification](#22-update-draft-verification)
- [23. Submit Verification](#23-submit-verification)
- [24. List / View My Verifications](#24-list--view-my-verifications)

### Admin — Verification Management
- [25. Admin Verification Endpoints](#25-admin-verification-endpoints)

### Course Authoring (Instructor)
- [26. Courses — Create / List / Get / Patch](#26-courses)
- [27. Course Metadata (Objectives / Prerequisites / Audiences — text fields)](#27-course-metadata)
- [28. Sections](#28-sections)
- [29. Section Content (single creation path)](#29-section-content)
- [30. Lectures (read / update / delete)](#30-lectures)
- [31. Quizzes (questions + answers)](#31-quizzes)
- [32. Assignments (questions + rubric)](#32-assignments)
- [33. Coding Exercises (language configs + test cases)](#33-coding-exercises)
- [34. Course Status Transitions](#34-course-status-transitions)

### Co-instructor Invitations
- [34A. Send Invite](#34a-send-invite)
- [34B. List Invites for a Course](#34b-list-invites-for-a-course)
- [34C. Revoke an Invite](#34c-revoke-an-invite)
- [34D. My Received Invites](#34d-my-received-invites)
- [34E. Accept an Invite](#34e-accept-an-invite)
- [34F. Decline an Invite](#34f-decline-an-invite)

### Public Catalog (No Auth)
- [35. Browse, Filter, and Sort the Catalog](#35-public-catalog)
- [35A. Course Categories](#35a-course-categories)

### Learner — Enrollment & Dashboard
- [36. Enrollment](#36-enrollment)
- [37. My Courses (Dashboard + Player Header)](#37-my-courses)

### Learner Consumption (`/learn/...`)
- [38. Curriculum Outline](#38-curriculum-outline)
- [39. Lecture Detail + Watch Progress](#39-lecture-detail--watch-progress)
- [40. Quiz Detail + Submission](#40-quiz-detail--submission)
- [41. Assignment Detail + Submission + Polling + Retry](#41-assignment-detail--submission)
- [42. Coding Exercise Detail + Run + Submit + Polling + Retry](#42-coding-exercise-detail--run--submit)

### Certificates
- [43. Certificates (Issuance, Verify, Download)](#43-certificates)

### Course Reviews & Ratings
- [44. Course Reviews & Ratings](#44-course-reviews--ratings)

### Reference
- [45. Common Error Responses](#45-common-error-responses)
- [46. Pagination](#46-pagination)
- [47. Quick Test Flows](#47-quick-test-flows)
- [48. Notes](#48-notes)

---

## 1. Base URL

All endpoints in this guide are rooted at a single API root:

```
http://127.0.0.1:8000/api/v1
```

Endpoint paths in every section below append the app prefix (`auth/...`,
`courses/...`, `verification/...`) to this root. Set this as the
`base_url` variable in your Postman environment (see Section 3).

## 2. Prerequisites

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Configure environment (`.env`):
   ```env
   CELERY_BROKER_URL=redis://127.0.0.1:6379/0
   CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
   MEDIA_URL=/media/
   MEDIA_ROOT=<absolute-path-to-project>/media
   FFMPEG_BINARY_PATH=<absolute-path-to-ffmpeg>
   FFPROBE_BINARY_PATH=<absolute-path-to-ffprobe>
   ```
3. Apply migrations:
   ```bash
   python manage.py migrate
   ```
4. Run the services:
   ```bash
   python manage.py runserver
   celery -A career_college_backend worker --loglevel=info --pool=solo
   ```
5. For course authoring you need at least one published course. For
   learner endpoints you need a verified learner account. See the Quick
   Test Flows in Section 45.

## 3. Postman Environment Variables

Create one Postman environment with:

```text
base_url        = http://127.0.0.1:8000/api/v1
access_token    = <fill after login as the relevant user>
course_slug     = python-backend-bootcamp
course_id       =
section_id      =
lecture_id      =
content_id      =
quiz_id         =
question_id     =
answer_id       =
exercise_id     =
config_id       =
tc_id           =
assignment_id   =
aq_id           =
category_id     =
certificate_uid =
review_id       =
admin_token     = <fill after login as an admin/staff user>
```

Multi-actor flows (learner + instructor + admin in the same Postman
collection) usually need three tokens — keep `{{access_token}}` for the
currently-logged-in user and copy/paste tokens between scenarios, or add
`learner_token` / `instructor_token` / `admin_token` variables and
reference them explicitly in the Authorization tab.

## 4. Required Headers

For authenticated endpoints:

```http
Authorization: Bearer {{access_token}}
Content-Type: application/json
```

Public endpoints (catalog browse, catalog detail, public profile lists)
do **not** require `Authorization`.

For file uploads (profile photo, ID verification documents, video
lectures), switch the Body type from **raw JSON** to **form-data** and
omit the `Content-Type` header — Postman sets the multipart boundary
automatically.

---

# Authentication

## 5. Register

**POST** `{{base_url}}/auth/register/`

### 5.1 Register — Learner

```json
{
    "email": "john.learner@example.com",
    "full_name": "John Doe",
    "password": "Secure@1234",
    "confirm_password": "Secure@1234",
    "user_type": "learner"
}
```

**Expected 201:**
```json
{
    "success": true,
    "message": "Registration successful. OTP sent to your email.",
    "data": {
        "user_id": 1,
        "email": "john.learner@example.com",
        "full_name": "John Doe",
        "user_type": "learner",
        "is_email_verified": false,
        "is_verified": true
    }
}
```

### 5.2 Register — Instructor

```json
{
    "email": "sarah.instructor@example.com",
    "full_name": "Sarah Williams",
    "password": "Teach@5678",
    "confirm_password": "Teach@5678",
    "user_type": "instructor"
}
```

**Expected 201:** Same shape as 5.1 with `"user_type": "instructor"` and `"is_verified": false` (instructors require admin verification).

### 5.3 Register — Partner Institution

```json
{
    "email": "admin@techuniversity.edu",
    "full_name": "Tech University",
    "password": "Partner@9012",
    "confirm_password": "Partner@9012",
    "user_type": "partner_institution",
    "institution_name": "Tech University",
    "institution_type": "university"
}
```

**`institution_type` options:**

| Value | Label |
|---|---|
| `university` | University |
| `college` | College |
| `training_center` | Training Center |
| `corporate` | Corporate Training |
| `nonprofit` | Non-Profit |
| `other` | Other |

---

## 6. Register — Validation Error Cases

### 6a. Missing `user_type`
```json
{ "email": "test@example.com", "full_name": "Test User", "password": "Secure@1234", "confirm_password": "Secure@1234" }
```
**Expected 400:** `user_type` — This field is required.

### 6b. Partner institution with a personal email
```json
{ "email": "partner@gmail.com", "user_type": "partner_institution", "institution_name": "Some Institute", "institution_type": "college", ... }
```
**Expected 400:** `email` — Partner institutions must register with an official institutional email address, not a personal email.

### 6c. Partner institution missing `institution_name`
**Expected 400:** `institution_name` — Institution name is required for partner institution registration.

### 6d. Partner institution missing `institution_type`
**Expected 400:** `institution_type` — Institution type is required for partner institution registration.

### 6e. Password mismatch
**Expected 400:** `confirm_password` — Passwords do not match.

### 6f. Duplicate email
**Expected 400:** `email` — A user with this email already exists.

---

## 7. Verify OTP

Required before login.

**POST** `{{base_url}}/auth/otp/verify/`

> Check your email or the database (`users` table → `otp_code` column) for the OTP.

```json
{
    "email": "john.learner@example.com",
    "otp": "123456",
    "purpose": "registration"
}
```

**Expected 200:**
```json
{ "success": true, "message": "Email verified successfully." }
```

---

## 8. Resend OTP

**POST** `{{base_url}}/auth/otp/resend/`

```json
{ "email": "john.learner@example.com", "purpose": "registration" }
```

**Expected 200:**
```json
{ "success": true, "message": "OTP resent successfully." }
```

---

## 9. Login

**POST** `{{base_url}}/auth/login/`

> User must have a verified email before login works.

```json
{
    "email": "john.learner@example.com",
    "password": "Secure@1234"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Login successful.",
    "data": {
        "user_id": 1,
        "email": "john.learner@example.com",
        "full_name": "John Doe",
        "user_type": "learner",
        "tokens": {
            "access": "<access_token>",
            "refresh": "<refresh_token>"
        }
    }
}
```

Save `data.tokens.access` as `{{access_token}}` in your Postman environment.

### Login error cases

**Unverified email** — Expected 401: Invalid credentials or account issue.

**Wrong password** — Expected 401: Invalid credentials or account issue.

---

## 10. Refresh Token

**POST** `{{base_url}}/auth/token/refresh/`

> Use this to mint a new access token when the current one expires. No `Authorization` header required.

```json
{ "refresh": "<refresh_token>" }
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Token refreshed successfully.",
    "tokens": { "access": "<new_access_token>", "refresh": "<new_refresh_token>" }
}
```

### Refresh error cases

**Expired or already-used refresh token** — Expected 401: `{ "success": false, "message": "Token is invalid or expired" }`

**Missing `refresh` field** — Expected 400: `{ "success": false, "message": "Token refresh failed.", "errors": { "refresh": ["This field is required."] } }`

---

## 11. Logout

**POST** `{{base_url}}/auth/logout/`

**Headers:** `Authorization: Bearer {{access_token}}`

**Body:**
```json
{ "refresh": "<refresh_token>" }
```

**Expected 200:**
```json
{ "success": true, "message": "Logged out successfully." }
```

---

## 12. Google Sign-In

### 12.1 Redirect

**GET** `{{base_url}}/auth/google/`

Open this URL in a **browser** (not Postman body). It redirects to Google's consent screen. In Postman, send a GET and check for a `302`.

**Optional query parameter:**

| Param | Default | Description |
|---|---|---|
| `user_type` | `learner` | Used for new account creation: `learner` or `instructor` |

**Examples:**
- `{{base_url}}/auth/google/` — signs in as learner (default)
- `{{base_url}}/auth/google/?user_type=instructor` — signs in as instructor

**Expected 302:**
- `Location` header points to `https://accounts.google.com/o/oauth2/v2/auth?...`
- Contains your `client_id`, `redirect_uri`, `scope=openid+email+profile`, and a random `state` param

**How to test in Postman:**
1. Create a GET request to `{{base_url}}/auth/google/`
2. In Postman **Settings** (gear icon), **disable** "Automatically follow redirects"
3. Send — you should get a `302` with the Google URL in the `Location` header
4. Copy that URL and open it in a browser to complete the consent flow

**Error case — not configured (503):**

If `GOOGLE_CLIENT_ID` is missing from `.env`:
```json
{ "success": false, "message": "Google sign-in is not configured on the server." }
```

### 12.2 Callback

**GET** `{{base_url}}/auth/google/callback/`

> This endpoint is called **by Google**, not by you directly. After consent, Google redirects with a `code` query parameter.

#### Backend-only mode (default — no frontend needed)

When `FRONTEND_GOOGLE_CALLBACK` is **not set** in `.env`, the callback handles the entire flow:

1. Receives `?code=<authorization_code>` from Google
2. Exchanges the code server-to-server
3. Fetches the Google profile
4. Creates or finds the user
5. Sets HttpOnly JWT cookies
6. Returns a JSON response

**Expected 200:**
```json
{
    "success": true,
    "message": "Google sign-in successful. New account created.",
    "data": {
        "user_id": 10,
        "email": "user@gmail.com",
        "full_name": "Google User",
        "user_type": "learner",
        "is_email_verified": true,
        "is_verified": true,
        "auth_provider": "google",
        "is_new_user": true
    }
}
```

> `user_type` defaults to `learner`. To sign in as instructor, start at `{{base_url}}/auth/google/?user_type=instructor`.

**Error — Google returned an error:** `GET /auth/google/callback/?error=access_denied`
**Expected 400:** `"Google sign-in was cancelled or failed."`

**Error — No `code` param:**
**Expected 400:** `"No authorization code received from Google."`

#### Frontend mode

When `FRONTEND_GOOGLE_CALLBACK` **is set** in `.env`, the callback redirects:

- Receives `?code=<authorization_code>&state=<state>`
- Redirects to `FRONTEND_GOOGLE_CALLBACK?code=<code>&state=<state>`
- On error, redirects to `FRONTEND_ERROR_URL?error=<message>`

**Postman test (frontend mode):**
```
GET {{base_url}}/auth/google/callback/?code=fake_auth_code_123&state=test_state
```
**Expected 302:** `Location: http://localhost:3000/auth/google/callback?code=fake_auth_code_123&state=test_state`

### 12.3 Exchange Token

**POST** `{{base_url}}/auth/google/exchange-token/`

> The main endpoint your frontend calls after receiving the authorization code from the callback redirect.

#### 12.3a New Learner Sign-In
```json
{ "code": "<authorization_code_from_google>", "user_type": "learner" }
```

**Expected 200:** Same shape as 12.2 with `is_new_user: true`.

**Response also sets HttpOnly cookies:**

| Cookie | HttpOnly | Secure | SameSite | Max-Age |
|---|---|---|---|---|
| `access_token` | ✅ | ✅* | Lax | 43200 (12h) |
| `refresh_token` | ✅ | ✅* | Lax | 604800 (7d) |

> *`Secure` is `False` in development (`DEBUG=True`), `True` in production.

#### 12.3b New Instructor Sign-In
```json
{ "code": "<authorization_code_from_google>", "user_type": "instructor" }
```

**Expected 200:** `user_type: "instructor"`, `is_verified: false` (instructors need admin verification).

#### 12.3c Existing User Sign-In
```json
{ "code": "<authorization_code_from_google>" }
```

If the Google email matches an existing user, no new account is created (`is_new_user: false`). `user_type` defaults to `"learner"` and is only used for new-account creation.

### 12.4 Google Sign-In Error Cases

| Scenario | Status | Message |
|---|---|---|
| Missing authorization code | 400 | `"Authorization code is required."` |
| Invalid / expired code | 400 | `"Failed to exchange the authorization code with Google."` |
| `user_type=partner_institution` | 400 | `errors.user_type` — only `learner`/`instructor` allowed |
| Existing `partner_institution` user | 403 | `"Partner institution accounts cannot sign in with Google."` |
| Deleted user | 403 | `"This account has been deleted and cannot sign in with Google."` |
| Deactivated / restricted user | 403 | `"Your account has been deactivated or restricted. Please contact support."` |
| Google sub already linked to another user | 409 | `"This Google account is already linked to another user."` |
| Existing user already linked to a different Google account | 409 | `"Your account is already linked to a different Google account."` |

---

# Password Management

## 13. Forgot Password

**POST** `{{base_url}}/auth/password/forgot/`

```json
{ "email": "john.learner@example.com" }
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Password reset OTP sent successfully.",
    "data": {
        "email": "john.learner@example.com",
        "purpose": "password_reset",
        "note": "OTP will expire in 2 minutes."
    }
}
```

### Forgot password error cases

**Email not found** — Expected 400: `email` — No account found with this email.

**Email not verified** — Expected 400: `email` — Email must be verified before password reset.

---

## 14. Verify OTP for Password Reset

**POST** `{{base_url}}/auth/otp/verify/`

```json
{
    "email": "john.learner@example.com",
    "otp": "123456",
    "purpose": "password_reset"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "OTP verified successfully! Now you can reset your password.",
    "data": {
        "user_id": 1,
        "email": "john.learner@example.com",
        "purpose": "password_reset",
        "reset_token": "<password_reset_token>",
        "token_expires_in": "15 minutes",
        "note": "Use this token with email to reset your password within 15 minutes."
    }
}
```

> Save `reset_token` for the next step.

---

## 15. Reset Password

**POST** `{{base_url}}/auth/password/reset/`

```json
{
    "email": "john.learner@example.com",
    "reset_token": "<password_reset_token>",
    "new_password": "NewSecure@1234",
    "confirm_password": "NewSecure@1234"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Password has been reset successfully. You can now login with your new password.",
    "data": {
        "email": "john.learner@example.com",
        "user_id": 1,
        "user_slug": "john-doe"
    }
}
```

### Reset password error cases

**Token expired or invalid** — Expected 400: `reset_token` — Invalid or expired reset token.

**Password mismatch** — Expected 400: `confirm_password` — Passwords do not match.

---

## 15A. Change Password (Authenticated User)

For users who already know their current password and want to change it
from inside their session. This is the *authenticated* flow — distinct
from the forgot/reset flow above (which is for users who can't log in).

**POST** `{{base_url}}/auth/password/change/`

**Headers:** `Authorization: Bearer {{access_token}}`

```json
{
    "current_password": "Secure@1234",
    "new_password": "EvenMoreSecure@5678",
    "confirm_password": "EvenMoreSecure@5678"
}
```

All three fields are required. Lengths must be between 8 and 128
characters. `new_password` runs through Django's full password validator
plus the project's custom strength check (same rules as registration).

**Expected 200:**
```json
{
    "success": true,
    "message": "Password updated successfully."
}
```

### Change password error cases

**Wrong current password:**
```json
{
    "current_password": "ThisIsTheWrongOne!",
    "new_password": "EvenMoreSecure@5678",
    "confirm_password": "EvenMoreSecure@5678"
}
```
**Expected 400:**
```json
{
    "success": false,
    "message": "Password change failed.",
    "errors": { "current_password": ["Current password is incorrect."] }
}
```

**New password mismatch:**
```json
{
    "current_password": "Secure@1234",
    "new_password": "EvenMoreSecure@5678",
    "confirm_password": "DifferentTypo@5678"
}
```
**Expected 400:** `errors.confirm_password: ["Passwords do not match."]`

**New password identical to current:**
**Expected 400:** `errors.new_password: ["New password must be different from current password."]`

**Weak new password** (too short, all numeric, common, or fails project-specific strength rules):
**Expected 400:** `errors.new_password: ["This password is too common.", ...]`

**Unauthenticated:**
**Expected 401:** Authentication credentials were not provided.

> Unlike the forgot/reset flow, no OTP is involved. The current password
> is the proof-of-identity. Existing JWTs are not auto-revoked by a
> password change — if you want to log the user out of all other devices
> after a successful change, follow up with the logout endpoint and have
> the frontend clear stored tokens.

---

# Profile Management

## 16. My Profile

### 16.1 Get My Profile

**GET** `{{base_url}}/auth/profile/me/`

**Headers:** `Authorization: Bearer {{access_token}}`

**Expected 200 (Learner):**
```json
{
    "success": true,
    "data": {
        "user": {
            "id": 1,
            "email": "john.learner@example.com",
            "full_name": "John Doe",
            "name_slug": "john-doe",
            "user_type": "learner",
            "is_email_verified": true,
            "is_verified": true,
            "registration_date": "2026-04-11T..."
        },
        "profile": {
            "id": 1,
            "profile_photo": null,
            "headline": "",
            "bio": "",
            "date_of_birth": null,
            "city": "",
            "state": "",
            "country": "",
            "experience_level": "",
            "learning_goal": "",
            "interests": [],
            "preferred_language": "English",
            "linkedin_url": "",
            "github_url": "",
            "website_url": "",
            "is_profile_public": true,
            "created_at": "...",
            "updated_at": "..."
        },
        "education": [],
        "work_experience": []
    }
}
```

**Errors:**
- No auth token → 401: Authentication credentials were not provided.
- Email not verified → 403: Your email must be verified before accessing this resource.

### 16.2 Update Profile (PATCH)

**PATCH** `{{base_url}}/auth/profile/me/`

#### 16.2a Update Learner Profile
```json
{
    "headline": "Data Analyst at Google",
    "bio": "Passionate about data science and machine learning.",
    "city": "San Francisco",
    "state": "California",
    "country": "USA",
    "experience_level": "mid",
    "learning_goal": "Switch to a career in data science",
    "interests": ["Python", "Machine Learning", "Data Visualization"],
    "linkedin_url": "https://linkedin.com/in/johndoe",
    "github_url": "https://github.com/johndoe"
}
```

#### 16.2b Update Instructor Profile
```json
{
    "headline": "Senior ML Engineer at Meta",
    "bio": "10+ years teaching machine learning.",
    "city": "New York",
    "country": "USA",
    "specialization": ["Deep Learning", "NLP", "Computer Vision"],
    "years_of_experience": 10,
    "current_title": "Senior ML Engineer",
    "current_organization": "Meta",
    "linkedin_url": "https://linkedin.com/in/sarahwilliams"
}
```

#### 16.2c Update Partner Institution Profile
```json
{
    "tagline": "Leading the future of tech education",
    "description": "A premier university focused on technology and innovation.",
    "city": "Boston",
    "state": "Massachusetts",
    "country": "USA",
    "contact_email": "admissions@techuniversity.edu",
    "contact_phone": "+1-555-0123",
    "website_url": "https://techuniversity.edu",
    "linkedin_url": "https://linkedin.com/school/techuniversity"
}
```

#### `experience_level` options (Learner):

| Value | Label |
|---|---|
| `student` | Student / No experience |
| `entry` | Entry level (0–2 years) |
| `mid` | Mid level (3–5 years) |
| `senior` | Senior level (6–10 years) |
| `expert` | Expert (10+ years) |

### 16.3 Upload Profile Photo / Logo (form-data)

**PATCH** `{{base_url}}/auth/profile/me/`

> File uploads must use **form-data** (Body → form-data), not raw JSON.

#### Learner / Instructor — Upload Profile Photo

| Key | Type | Value |
|---|---|---|
| `profile_photo` | File | *(select an image file)* |
| `headline` | Text | Data Analyst at Google |

**Expected 200:** Profile updated with `profile_photo: "/learner_profiles/photos/..."`

#### Partner Institution — Upload Logo & Cover Image

| Key | Type | Value |
|---|---|---|
| `logo` | File | *(select a logo image file)* |
| `cover_image` | File | *(select a cover image file)* |
| `tagline` | Text | Leading the future of tech |

#### Remove Profile Photo (set to null)

| Key | Type | Value |
|---|---|---|
| `profile_photo` | File | *(leave empty)* |

#### Postman form-data tips

1. **Body** → **form-data**
2. For file fields: hover over the **Key** cell, click the type dropdown, change from **Text** to **File**
3. Click **Select Files** in the **Value** cell to pick an image
4. You can mix file and text fields in the same request
5. **Do NOT** set `Content-Type` header manually — Postman sets `multipart/form-data` with the correct boundary

---

## 17. Education

### 17.1 List My Education

**GET** `{{base_url}}/auth/profile/me/education/`

**Headers:** `Authorization: Bearer {{access_token}}`

**Expected 200:**
```json
{ "success": true, "data": [] }
```

### 17.2 Create Education Entry

**POST** `{{base_url}}/auth/profile/me/education/`

```json
{
    "degree": "bachelor",
    "field_of_study": "Computer Science",
    "institution": "MIT",
    "start_date": "2018-09-01",
    "end_date": "2022-06-15",
    "is_current": false
}
```

**Expected 201:**
```json
{
    "success": true,
    "message": "Education entry created.",
    "data": {
        "id": 1,
        "degree": "bachelor",
        "field_of_study": "Computer Science",
        "institution": "MIT",
        "start_date": "2018-09-01",
        "end_date": "2022-06-15",
        "is_current": false,
        "created_at": "...",
        "updated_at": "..."
    }
}
```

### 17.3 Current Education (no `end_date`)

```json
{
    "degree": "master",
    "field_of_study": "Data Science",
    "institution": "Stanford University",
    "start_date": "2024-09-01",
    "is_current": true
}
```

### `degree` options:

| Value | Label |
|---|---|
| `high_school` | High School |
| `associate` | Associate Degree |
| `bachelor` | Bachelor's Degree |
| `master` | Master's Degree |
| `doctorate` | Doctorate |
| `diploma` | Diploma |
| `certificate` | Certificate |
| `other` | Other |

### 17.4 Update / Delete Education Entry

**PATCH** `{{base_url}}/auth/profile/me/education/1/`
```json
{ "field_of_study": "Computer Science & Engineering" }
```
**Expected 200:** Education entry updated.

**DELETE** `{{base_url}}/auth/profile/me/education/1/`
**Expected 200:** Education entry deleted.

### Education validation errors

**Current education with `end_date`:** Expected 400: `end_date` — Current education should not have an end date.

**Completed education without `end_date`:** Expected 400: `end_date` — End date is required for completed education.

**Partner institution user trying to add education:** Expected 403: Education entries are only available for learners and instructors.

---

## 18. Work Experience

### 18.1 List

**GET** `{{base_url}}/auth/profile/me/work-experience/`

**Expected 200:** `{ "success": true, "data": [] }`

### 18.2 Create

**POST** `{{base_url}}/auth/profile/me/work-experience/`

```json
{
    "job_title": "Data Analyst",
    "company": "Google",
    "location": "San Francisco, CA",
    "start_date": "2022-07-01",
    "is_current": true
}
```

**Expected 201:** Standard envelope with the created row (`end_date: null` while `is_current: true`).

### 18.3 Past Position

```json
{
    "job_title": "Junior Developer",
    "company": "Startup Inc.",
    "location": "Remote",
    "start_date": "2020-01-15",
    "end_date": "2022-06-30",
    "is_current": false
}
```

### 18.4 Update / Delete

**PATCH** `{{base_url}}/auth/profile/me/work-experience/1/`
```json
{ "job_title": "Senior Data Analyst" }
```

**DELETE** `{{base_url}}/auth/profile/me/work-experience/1/`

### Validation errors

- Current position with `end_date` → 400: Current position should not have an end date.
- Past position without `end_date` → 400: End date is required for past positions.

---

# Public Profiles

## 19. Public Profile by Slug

**GET** `{{base_url}}/auth/profiles/<slug>/`

> No authentication required. Replace `<slug>` with the user's `name_slug`.

**Example:** `GET {{base_url}}/auth/profiles/john-doe/`

**Expected 200 (Learner):**
```json
{
    "success": true,
    "data": {
        "user_type": "learner",
        "full_name": "John Doe",
        "slug": "john-doe",
        "profile_photo": null,
        "headline": "Data Analyst at Google",
        "bio": "Passionate about data science and machine learning.",
        "city": "San Francisco",
        "state": "California",
        "country": "USA",
        "experience_level": "mid",
        "learning_goal": "Switch to a career in data science",
        "interests": ["Python", "Machine Learning", "Data Visualization"],
        "linkedin_url": "https://linkedin.com/in/johndoe",
        "github_url": "https://github.com/johndoe",
        "website_url": "",
        "education": [
            { "degree": "bachelor", "field_of_study": "Computer Science", "institution": "MIT", "start_date": "2018-09-01", "end_date": "2022-06-15", "is_current": false }
        ],
        "work_experience": [
            { "job_title": "Data Analyst", "company": "Google", "location": "San Francisco, CA", "start_date": "2022-07-01", "end_date": null, "is_current": true }
        ]
    }
}
```

### Errors

- Non-existent slug → 404: Profile not found.
- Learner with `is_profile_public: false` → 404: Profile not found.

---

## 20. Browse Public Profile Lists

### 20.1 Browse Learners

**GET** `{{base_url}}/auth/profiles/learners/`

> No authentication required. Supports pagination and filters.

| Param | Example | Description |
|---|---|---|
| `page` | `1` | Page number |
| `page_size` | `10` | Results per page (max 100) |
| `country` | `USA` | Filter by country (case-insensitive) |
| `experience_level` | `mid` | Filter by experience level |

**Example:** `GET {{base_url}}/auth/profiles/learners/?country=USA&experience_level=mid`

**Expected 200:**
```json
{
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
        {
            "full_name": "John Doe",
            "slug": "john-doe",
            "profile_photo": null,
            "headline": "Data Analyst at Google",
            "country": "USA",
            "experience_level": "mid"
        }
    ]
}
```

> Only learners with `is_profile_public: true` and verified email are shown.

### 20.2 Browse Instructors

**GET** `{{base_url}}/auth/profiles/instructors/`

| Param | Example | Description |
|---|---|---|
| `page` | `1` | Page number |
| `page_size` | `10` | Results per page (max 100) |
| `country` | `USA` | Case-insensitive |
| `is_verified` | `true` | Filter by verification status |

**Expected 200:**
```json
{
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
        {
            "full_name": "Sarah Williams",
            "slug": "sarah-williams",
            "profile_photo": null,
            "headline": "Senior ML Engineer at Meta",
            "country": "USA",
            "specialization": ["Deep Learning", "NLP"],
            "is_verified": true
        }
    ]
}
```

### 20.3 Browse Institutions

**GET** `{{base_url}}/auth/profiles/institutions/`

| Param | Example | Description |
|---|---|---|
| `page` | `1` | Page number |
| `page_size` | `10` | Results per page (max 100) |
| `country` | `USA` | Case-insensitive |
| `institution_type` | `university` | Filter by institution type |

**Expected 200:**
```json
{
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
        {
            "institution_name": "Tech University",
            "slug": "tech-university",
            "logo": null,
            "tagline": "Leading the future of tech education",
            "institution_type": "university",
            "country": "USA",
            "is_verified": false
        }
    ]
}
```

---

# Instructor ID Verification

> Only instructors with verified email can use these endpoints. Login as an instructor first.

### Verification Lifecycle

```
draft → submitted → under_review → approved
                                  → rejected
                                  → action_required → submitted (resubmit)
```

- **draft** — instructor is filling in details (can edit freely)
- **submitted** — waiting for admin to pick up
- **under_review** — admin is reviewing
- **approved** — instructor is now verified
- **rejected** — denied with a reason
- **action_required** — admin asked instructor to fix something (instructor can edit and resubmit)

### `document_type` options:

| Value | Label |
|---|---|
| `national_id` | National ID Card |
| `passport` | Passport |
| `drivers_license` | Driver's License |
| `residence_permit` | Residence Permit |

## 21. Create Draft Verification

**POST** `{{base_url}}/verification/create/`

**Headers:** `Authorization: Bearer {{access_token}}` (instructor)

> All fields are optional at this stage. You can create an empty draft and fill in details later.

### 21a. Create empty draft
```json
{}
```

**Expected 201:**
```json
{
    "success": true,
    "message": "Draft verification created.",
    "data": {
        "id": 1,
        "document_type": "",
        "document_number": "",
        "issuing_country": "",
        "expiry_date": null,
        "document_front": null,
        "document_back": null,
        "selfie": null,
        "resume": null,
        "status": "draft",
        "rejection_reason": "",
        "action_required_reason": "",
        "reviewed_by_email": null,
        "reviewed_at": null,
        "created_at": "...",
        "submitted_at": null,
        "updated_at": "..."
    }
}
```

### 21b. Create draft with partial data
```json
{ "document_type": "passport", "issuing_country": "USA" }
```

### Create verification error cases

- Non-instructor user → 403: Only instructors can access this resource.
- Already has an active request (`draft`/`submitted`/`under_review`/`action_required`) → 400: `"You already have a verification request in progress."`
- Email not verified → 403: Your email must be verified before accessing this resource.

---

## 22. Update Draft Verification

**PATCH** `{{base_url}}/verification/1/update/`

> Replace `1` with your verification ID. Only works when status is `draft` or `action_required`.

### 22a. Update text fields (JSON)
```json
{
    "document_type": "national_id",
    "document_number": "AB1234567",
    "issuing_country": "Bangladesh",
    "expiry_date": "2030-12-31"
}
```

### 22b. Upload documents (form-data)

> File uploads must use **form-data** (not raw JSON).

**PATCH** `{{base_url}}/verification/1/update/`

| Key | Type | Value |
|---|---|---|
| `document_front` | File | *(front image of your ID)* |
| `document_back` | File | *(back image, if applicable)* |
| `selfie` | File | *(selfie holding your ID)* |
| `resume` | File | *(resume/CV, optional)* |

**Expected 200:** Same structure with file URLs populated.

### 22c. Full update with PUT (all fields required)

**PUT** `{{base_url}}/verification/1/update/`

Use **form-data** to include both text and file fields.

### Update verification error cases

- Verification not in editable status (e.g. already submitted) → 404: Not found.
- Another user's verification → 404: Not found.
- Expired document (`expiry_date: "2020-01-01"`) → 400: `expiry_date` — Document has already expired.

---

## 23. Submit Verification

**POST** `{{base_url}}/verification/1/submit/`

> Transitions from `draft` → `submitted` or `action_required` → `submitted`.

**Headers:** `Authorization: Bearer {{access_token}}` (instructor)
**Body:** *(empty)*

### Prerequisites

**1. Instructor profile must be complete** — these profile fields must be filled:

| Field | Description |
|---|---|
| `headline` | Professional tagline |
| `bio` | Professional biography (non-empty) |
| `specialization` | At least one area of expertise |
| `years_of_experience` | Must be greater than 0 |
| `current_title` | Current job title |

> Update your profile first via Section 16.2b.

**2. Verification document fields must be filled:**

| Field | Required |
|---|---|
| `document_type` | Yes |
| `document_number` | Yes |
| `issuing_country` | Yes |
| `document_front` | Yes |
| `selfie` | Yes |
| `document_back` | No |
| `expiry_date` | No |
| `resume` | No |

**Expected 200:**
```json
{
    "success": true,
    "message": "Verification submitted successfully.",
    "data": {
        "id": 1,
        "status": "submitted",
        "submitted_at": "2026-04-13T...",
        "...other fields..."
    }
}
```

### Submit error cases

**Incomplete instructor profile:**
```json
{
    "success": false,
    "message": "Your profile must be complete before submitting for verification.",
    "errors": {
        "profile": {
            "headline": "Headline is required.",
            "specialization": "At least one specialization is required."
        }
    }
}
```

**Missing required document fields:** 400 with a message naming the missing fields.

**Verification already submitted:** 404. (Only `draft` and `action_required` can be submitted.)

---

## 24. List / View My Verifications

### 24.1 List

**GET** `{{base_url}}/verification/my/`

**Expected 200:** Array of all verifications for the logged-in instructor (most recent first).

### 24.2 View Single

**GET** `{{base_url}}/verification/my/1/`

**Errors:**
- Verification belongs to another user → 404: Not found.

---

# Admin — Verification Management

> These endpoints require an admin (staff) user. Login as a superuser or staff account.

```bash
python manage.py createsuperuser
```

## 25. Admin Verification Endpoints

### 25.1 List All Verifications

**GET** `{{base_url}}/verification/admin/list/`

**Expected 200 (paginated):**
```json
{
    "count": 1,
    "next": null,
    "previous": null,
    "results": [
        {
            "id": 1,
            "instructor_name": "Sarah Williams",
            "instructor_email": "sarah.instructor@example.com",
            "document_type": "national_id",
            "issuing_country": "Bangladesh",
            "status": "submitted",
            "submitted_at": "..."
        }
    ]
}
```

**Filter by status:** `GET {{base_url}}/verification/admin/list/?status=submitted`

| Status | Description |
|---|---|
| `draft` | Not yet submitted |
| `submitted` | Waiting for admin review |
| `under_review` | Admin is reviewing |
| `action_required` | Instructor needs to fix |
| `approved` | Verified |
| `rejected` | Denied |
| `expired` | Expired |

**Errors:** Non-admin user → 403: Admin access required.

### 25.2 View Verification Detail

**GET** `{{base_url}}/verification/admin/1/`

**Expected 200:** Full verification record including admin-only fields (`admin_notes`).

### 25.3 Review Verification

**POST** `{{base_url}}/verification/admin/1/review/`

#### 25.3a Pick Up (submitted → under_review)
```json
{ "action": "pick_up" }
```

#### 25.3b Approve (under_review → approved)
```json
{ "action": "approve", "admin_notes": "Documents verified. All clear." }
```

> Approving also sets `InstructorProfile.is_verified = True`.

#### 25.3c Reject (under_review → rejected)
```json
{
    "action": "reject",
    "rejection_reason": "Document image is blurry and unreadable.",
    "admin_notes": "Asked to resubmit with clearer photos."
}
```

**Missing `rejection_reason`:** 400 with `errors.rejection_reason: "A reason is required when rejecting."`

#### 25.3d Request Action (under_review → action_required)
```json
{
    "action": "request_action",
    "action_required_reason": "Selfie does not match the ID photo. Please retake.",
    "admin_notes": "Possible photo mismatch — give another chance."
}
```

**Missing `action_required_reason`:** 400 with `errors.action_required_reason: "A reason is required when requesting action."`

#### 25.3e Expire
```json
{ "action": "expire" }
```

### Admin action options:

| Action | From | To | Required |
|---|---|---|---|
| `pick_up` | submitted | under_review | — |
| `approve` | under_review | approved | — |
| `reject` | under_review | rejected | `rejection_reason` |
| `request_action` | under_review | action_required | `action_required_reason` |
| `expire` | submitted, under_review, action_required | expired | — |

### Invalid transition errors

- Approve a draft → 400: `Cannot transition from "draft" to "approved". Allowed: submitted.`
- Pick up an approved request → 400: `Cannot transition from "approved" to "under_review". Allowed: none (terminal state).`

---

# Course Authoring (Instructor)

> All endpoints in Sections 26–34 require a **verified-instructor or verified-partner-institution** JWT (`IsVerifiedCourseCreator`). Non-owners get `404` (not `403`) so the existence of the course is not leaked. Partner institution users can create, list, and edit their own courses; co-instructors can read and edit content but cannot modify the instructor roster.

## 26. Courses

### 26.1 Create Course

**POST** `{{base_url}}/courses/create/`

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

`category` is **required** on create (must be an **active** category id — see Section 35A). Omitting
it → 400 `errors.category: ["This field is required."]`.

**Expected 201:**
```json
{
    "success": true,
    "message": "Course created successfully.",
    "data": { "id": 101, "title": "Python Backend Bootcamp" }
}
```

Save `data.id` as `{{course_id}}`.

### 26.2 List Courses

**GET** `{{base_url}}/courses/`

### 26.3 Get Course Detail

**GET** `{{base_url}}/courses/{{course_id}}/`

### 26.4 Patch Course

**PATCH** `{{base_url}}/courses/{{course_id}}/`

```json
{ "title": "Python Backend Bootcamp (Updated)", "price": "89.99" }
```

> `status`, `rejection_reason`, `partner_institution`, and `instructors` are not writable via POST or PATCH. `partner_institution` is auto-set from the creating user's profile when a partner institution account creates a course. Co-instructors can only be added via the invitation flow (Section 34A). Use the dedicated transition endpoints in Section 34 for status changes.

---

## 27. Course Metadata

`learning_objectives`, `prerequisites`, and `audiences` are **plain text fields on the course**
(newline-separated — one item per line; the frontend splits on `\n`). There are **no separate
metadata endpoints** — set them directly in the course create/PATCH payload (Section 26), and read
them back on any course detail response.

### 27.1 Set on Create

**POST** `{{base_url}}/courses/create/`

```json
{
    "title": "Python Backend Bootcamp",
    "description": "Build production APIs with Django and DRF.",
    "learning_objectives": "Build production REST APIs.\nContainerize with Docker.",
    "prerequisites": "Comfortable with Python.",
    "audiences": "Backend engineers.\nAspiring DevOps engineers."
}
```

### 27.2 Update

**PATCH** `{{base_url}}/courses/{{course_id}}/`

Supplying any of the three fields **replaces that field's whole value** (it's a single string).

```json
{ "learning_objectives": "Design REST endpoints.\nWrite tests." }
```

### 27.3 Read

Any course detail/list response (`GET /courses/{{course_id}}/`, `/catalog/<slug>/`,
`/my-courses/<slug>/`) returns the three as strings, e.g. `"learning_objectives": "A\nB"`.
Empty when unset (`""`).

> Removed: the old `learning-objectives/`, `prerequisites/`, `audiences/` sub-resource endpoints
> and the `objective_id`/`prerequisite_id`/`audience_id` variables no longer exist — the three
> normalized tables were collapsed into these text fields.

---

## 28. Sections

### 28.1 Create Section

**POST** `{{base_url}}/courses/{{course_id}}/sections/create/`

```json
{
    "title": "Getting Started",
    "description": "Core setup and project structure",
    "position": 1
}
```

**Expected 201:** Save `data.id` as `{{section_id}}`.

### 28.2 List Sections

**GET** `{{base_url}}/courses/{{course_id}}/sections/`

Optional: `?ordering=position` or `?ordering=-position`.

### 28.3 Get / Update / Delete Section

- **GET** `{{base_url}}/courses/sections/{{section_id}}/`
- **PATCH** `{{base_url}}/courses/sections/{{section_id}}/`
- **PUT** `{{base_url}}/courses/sections/{{section_id}}/`
- **DELETE** `{{base_url}}/courses/sections/{{section_id}}/`

---

## 29. Section Content

All lectures, quizzes, coding exercises, and assignments **must** be created through this endpoint. There are no separate creation endpoints for individual content types.

### 29.1 Create Article Lecture

**POST** `{{base_url}}/courses/sections/{{section_id}}/contents/`

```json
{
    "item_type": "lecture",
    "title": "REST Fundamentals",
    "lecture_type": "article",
    "article_content": "HTTP methods, status codes, and API design basics.",
    "position": 1
}
```

**Expected 201:**
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
        "content": { "id": 301, "title": "REST Fundamentals", "lecture_type": "article" }
    }
}
```

Save `data.object_id` as `{{lecture_id}}`.

### 29.2 Create Video Lecture (multipart/form-data)

**POST** `{{base_url}}/courses/sections/{{section_id}}/contents/`

**Body type:** `form-data`

| Key | Value |
|---|---|
| `item_type` | `lecture` |
| `title` | `Intro Video` |
| `lecture_type` | `video` |
| `video_file` | *(select file)* |
| `position` | `2` (optional) |

After creation the video is queued for transcoding. Poll `GET {{base_url}}/courses/lectures/{{lecture_id}}/` until `active_video_asset.status` is `ready` or `failed`.

### 29.3 Create Quiz via Section Content

**POST** `{{base_url}}/courses/sections/{{section_id}}/contents/`

```json
{
    "item_type": "quiz",
    "title": "REST Basics Quiz",
    "description": "Checks understanding of HTTP and endpoints.",
    "position": 2
}
```

Save `data.id` as `{{content_id}}` and `data.object_id` as `{{quiz_id}}`.

### 29.4 Create Coding Exercise via Section Content

**POST** `{{base_url}}/courses/sections/{{section_id}}/contents/`

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

Save `data.object_id` as `{{exercise_id}}`.

### 29.5 Create Assignment via Section Content

**POST** `{{base_url}}/courses/sections/{{section_id}}/contents/`

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

Save `data.id` as `{{content_id}}` and `data.object_id` as `{{assignment_id}}`.

**Field semantics:**
- `total_score` is the instructor-declared "this assignment is worth N points" value (the denominator the learner sees and the figure `passing_score` is measured against).
- `passing_score` must be `<= total_score`. Mismatch → 400 with `errors.passing_score`.
- `total_score` is **independent** of `sum(question.points)`. The questions are a sub-allocation guide; the authoring UI can compare `total_score` against the response's `max_score` (sum of `question.points`) to flag under-/over-funded rubrics.

### 29.6 List Ordered Curriculum

**GET** `{{base_url}}/courses/sections/{{section_id}}/contents/`

Returns all content items ordered by `position`.

### 29.7 Reorder Curriculum Item

**PATCH** `{{base_url}}/courses/contents/{{content_id}}/reorder/`

```json
{ "position": 1 }
```

Item moves to the target position; other items shift automatically. No empty target slots needed.

---

## 30. Lectures

Create lectures via `sections/{id}/contents/` (Section 29). These endpoints are for reading and modifying existing lectures.

### 30.1 List Lectures in a Section

**GET** `{{base_url}}/courses/sections/{{section_id}}/lectures/`

### 30.2 Get / Patch / Put / Delete Lecture

- **GET** `{{base_url}}/courses/lectures/{{lecture_id}}/`
- **PATCH** `{{base_url}}/courses/lectures/{{lecture_id}}/` — `{ "title": "REST Fundamentals (Updated)" }`
- **PUT** `{{base_url}}/courses/lectures/{{lecture_id}}/`
- **DELETE** `{{base_url}}/courses/lectures/{{lecture_id}}/`

---

## 31. Quizzes

Create quizzes via `sections/{id}/contents/` (Section 29.3). These endpoints handle questions and answers.

### 31.1 Get / Patch / Delete Quiz

- **GET** `{{base_url}}/courses/quizzes/{{quiz_id}}/`
- **PATCH** `{{base_url}}/courses/quizzes/{{quiz_id}}/` — `{ "title": "Django ORM Quiz (Updated)" }`
- **DELETE** `{{base_url}}/courses/quizzes/{{quiz_id}}/`

### 31.2 Create Quiz Question

**POST** `{{base_url}}/courses/quizzes/{{quiz_id}}/questions/`

```json
{
    "question_text": "Which method returns exactly one object and throws if missing?",
    "position": 1
}
```

Save `data.id` as `{{question_id}}`.

### 31.3 List Quiz Questions

**GET** `{{base_url}}/courses/quizzes/{{quiz_id}}/questions/`

### 31.4 Update/Delete Question

- **PATCH** `{{base_url}}/courses/quiz-questions/{{question_id}}/`
- **DELETE** `{{base_url}}/courses/quiz-questions/{{question_id}}/`

### 31.5 Create Quiz Answers

**POST** `{{base_url}}/courses/quiz-questions/{{question_id}}/answers/`

Correct option:
```json
{ "answer_text": "get()", "is_correct": true }
```

Incorrect option:
```json
{ "answer_text": "filter()", "is_correct": false }
```

Save the first answer's id as `{{answer_id}}`.

### 31.6 List / Update / Delete Answers

- **GET** `{{base_url}}/courses/quiz-questions/{{question_id}}/answers/`
- **PATCH** `{{base_url}}/courses/quiz-answers/{{answer_id}}/`
- **DELETE** `{{base_url}}/courses/quiz-answers/{{answer_id}}/`

---

## 32. Assignments

Assignments are open-ended (free-text) questions with instructor-provided model answers. Like lectures, quizzes, and coding exercises, an assignment **must be created through the section-content endpoint** (Section 29.5). The dedicated `/assignments/...` URLs handle list / read / update / delete and the question sub-resource.

All write endpoints require a verified-instructor JWT. `model_answer` on a question is instructor-only and is stripped from learner-facing responses.

### 32.1 List Assignments in a Section

**GET** `{{base_url}}/courses/sections/{{section_id}}/assignments/`

Returns assignments belonging to that section, newest first. Each row includes nested `questions`, the instructor-declared `total_score`, and a computed `max_score` (sum of `question.points`).

### 32.2 Get Assignment Detail

**GET** `{{base_url}}/courses/assignments/{{assignment_id}}/`

**Expected 200:**
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

> The dedicated assignment endpoint **does not accept POST**. `POST {{base_url}}/courses/sections/{{section_id}}/assignments/` returns `405`. Always create via Section 29.5.

### 32.3 Patch Assignment

**PATCH** `{{base_url}}/courses/assignments/{{assignment_id}}/`

```json
{
    "title": "Reflection Essay (Updated)",
    "total_score": 120,
    "passing_score": 70
}
```

Allowed partial-update fields: `title`, `description`, `instructions`, `total_score`, `passing_score`. Cross-field rule on partials: updating `passing_score` alone to a value greater than the stored `total_score` → 400.

### 32.4 Delete Assignment

**DELETE** `{{base_url}}/courses/assignments/{{assignment_id}}/`

Deletes the assignment, cascades all its questions, and removes its `SectionContent` slot automatically.

### 32.5 Add Assignment Question

**POST** `{{base_url}}/courses/assignments/{{assignment_id}}/questions/`

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

**Expected 201:** Save `data.id` as `{{aq_id}}`. `position` is server-assigned.

**Rubric authoring rules (enforced by the serializer):**
- `sum(criterion.points)` must equal `question.points`. Mismatch → 400.
- Supported `type` values: `keyword`, `regex`, `min_length`, `max_length`, `any_of`, `all_of`. Unknown `type` → 400.
- `regex` criteria are compiled at save time; an unparseable pattern → 400.
- `case_sensitive` (optional, default `false`) is only honoured by `keyword` and `regex`.
- An empty rubric (`"rubric": []`) is allowed during draft authoring but will produce a `score=0` submission once published.
- `rubric` is **instructor-only** in the response. Non-instructors get the question without it (and without `model_answer`).

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

### 32.6 List / Get / Patch / Delete Question

- **GET (list)** `{{base_url}}/courses/assignments/{{assignment_id}}/questions/`
- **GET** `{{base_url}}/courses/assignment-questions/{{aq_id}}/`
- **PATCH** `{{base_url}}/courses/assignment-questions/{{aq_id}}/`
  Allowed fields: `question_text`, `model_answer`, `points`, `hint`.
- **DELETE** `{{base_url}}/courses/assignment-questions/{{aq_id}}/` — deletes and compacts trailing positions.

### 32.7 Reorder Assignment Questions

**PATCH** `{{base_url}}/courses/assignments/{{assignment_id}}/questions/reorder/`

```json
{ "ordered_ids": [3, 1, 2] }
```

Positions are reassigned to match the order of `ordered_ids`.

### 32.8 Assignment Validation Errors

| Bad input | Status | Error |
|---|---|---|
| Title too short (`"A"`) | 400 | `title: ["Assignment title must be at least 2 characters long."]` |
| Title missing | 400 | `title: ["This field is required."]` |
| Empty `question_text` | 400 | `question_text: ["Question text cannot be empty."]` |
| `ordered_ids` mismatched | 400 | `"ordered_ids must match the questions belonging to this assignment."` |
| `ordered_ids` duplicates | 400 | `"ordered_ids contains duplicates."` |
| Empty `ordered_ids` | 400 | `"ordered_ids must be a non-empty list."` |
| Non-integer ids | 400 | `"ordered_ids must contain integers only."` |

### 32.9 Auth & Ownership Error Cases

- Unauthenticated create → 401.
- Unverified instructor → 403: `"Only verified instructors or verified partner institutions can perform this action."`
- Learner trying to create → 403.
- Verified instructor not on the course → 404 (existence not leaked).
- Cross-instructor read → 404.
- Unverified instructor patches an assignment they own → 403 on patch (GET still allowed).
- POST to dedicated assignment list endpoint → 405. Use `sections/{id}/contents/`.

---

## 33. Coding Exercises

Create coding exercises via `sections/{id}/contents/` (Section 29.4). All endpoints below require a verified-instructor JWT. The exercise must belong to a section of a course you instruct — otherwise 404.

### 33.1 Get / Patch / Delete Coding Exercise

- **GET** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/`
- **PATCH** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/`
  ```json
  { "difficulty": "hard", "time_limit_ms": 5000 }
  ```
- **DELETE** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/` — cascades the `SectionContent` slot automatically.

### 33.2 Add Language Config

**POST** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/language-configs/`

```json
{
    "language": "python",
    "starter_code": "def two_sum(nums, target):\n    pass",
    "solution_code": "def two_sum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        if target - n in seen:\n            return [seen[target - n], i]\n        seen[n] = i"
}
```

Save `data.id` as `{{config_id}}`. Valid `language` values: `python`, `javascript`, `cpp`, `java`.

### 33.3 Duplicate Language Config (error case)

Repeating the POST with `"language": "python"` → 400: `"A config for this language already exists on this exercise."`

### 33.4 List / Get / Patch / Delete Language Config

- **GET** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/language-configs/`
- **GET** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/language-configs/{{config_id}}/`
- **PATCH** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/language-configs/{{config_id}}/`
- **DELETE** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/language-configs/{{config_id}}/`

### 33.5 Add Test Case

**POST** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/testcases/`

Visible case:
```json
{
    "input_data": "[2,7,11,15]\n9",
    "expected_output": "[0,1]",
    "is_hidden": false,
    "explanation": "nums[0] + nums[1] == 9",
    "position": 1
}
```

Hidden / grading-only case:
```json
{
    "input_data": "[3,2,4]\n6",
    "expected_output": "[1,2]",
    "is_hidden": true,
    "explanation": "",
    "position": 2
}
```

Save `data.id` as `{{tc_id}}`.

### 33.6 Duplicate Test Case Position (error case)

Repeating POST with `"position": 1` → 400: `"A test case already exists at that position for this exercise."`

### 33.7 List / Get / Patch / Delete Test Case

- **GET** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/testcases/` (ordered by `position`)
- **GET** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/testcases/{{tc_id}}/`
- **PATCH** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/testcases/{{tc_id}}/`
- **DELETE** `{{base_url}}/courses/coding-exercises/{{exercise_id}}/testcases/{{tc_id}}/`

### 33.8 Coding Exercise Validation Errors

| Bad input | Status | Error |
|---|---|---|
| `default_language` not in `supported_languages` | 400 | `default_language: ["default_language must be in supported_languages."]` |
| Empty `supported_languages` | 400 | `supported_languages: ["supported_languages must be a non-empty list."]` |
| Invalid language value (e.g. `"ruby"`) | 400 | `supported_languages: ["Invalid languages: ['ruby']. Must be one of ['python', 'javascript', 'cpp', 'java']."]` |
| Title too short (`"AB"`) | 400 | `title: ["Title must be at least 3 characters long."]` |

---

## 34. Course Status Transitions

### 34.1 Submit Course for Review (Instructor)

**POST** `{{base_url}}/courses/{{course_id}}/submit/`

**Body:** *(empty)*
**Headers:** verified instructor **or** verified partner institution JWT.

**Expected 200:**
```json
{
    "success": true,
    "message": "Course submitted for review.",
    "data": { "id": 101, "status": "under_review" }
}
```

Returns 400 with an `errors` dict if completeness checks fail (missing title/description, empty section, pending videos, incomplete quizzes).

### 34.2 Admin Approve Course

**POST** `{{base_url}}/courses/{{course_id}}/review/`

**Headers:** admin JWT (`is_staff` or `user_type: admin`).

```json
{ "action": "approve" }
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Course approved successfully.",
    "data": { "id": 101, "status": "published" }
}
```

### 34.3 Admin Reject Course

**POST** `{{base_url}}/courses/{{course_id}}/review/`

```json
{
    "action": "reject",
    "rejection_reason": "Missing captions on lecture 3. Please add subtitles."
}
```

`rejection_reason` is **required** when action is `reject`. Omitting it returns 400.

### 34.4 Instructor Rework a Rejected Course (back to Draft)

**POST** `{{base_url}}/courses/{{course_id}}/rework/`

**Body:** *(empty)*
**Headers:** verified instructor **or** verified partner institution JWT (must be owner or assigned instructor).

**Expected 200:**
```json
{
    "success": true,
    "message": "Course moved back to draft for reworking.",
    "data": { "id": 101, "status": "draft" }
}
```

Only works when current status is `rejected`. Any other status → 400.

### 34.5 Archive a Published Course

**POST** `{{base_url}}/courses/{{course_id}}/archive/`

**Body:** *(empty)*
**Headers:** verified instructor, verified partner institution, or admin JWT.

**Expected 200:**
```json
{
    "success": true,
    "message": "Course archived successfully.",
    "data": { "id": 101, "status": "archived" }
}
```

Only works when current status is `published`.

### 34.6 Invalid Transition (error case)

`POST /courses/{{course_id}}/submit/` on a course already `under_review`:

**Expected 400:** `"Cannot transition from \"under_review\" to \"under_review\". Allowed: published, rejected."`

---

# Co-instructor Invitations

> Sections 34A–34F cover the invitation flow. Owner endpoints require a **verified-instructor or verified-partner-institution** JWT where the caller is the course owner. Invitee endpoints require a **verified-instructor** JWT.

## 34A. Send Invite

**POST** `{{base_url}}/courses/{{course_id}}/instructors/invite/`

**Headers:** owner JWT.

```json
{ "email": "co.instructor@example.com" }
```

**Expected 201:**
```json
{
    "success": true,
    "message": "Invite sent successfully.",
    "data": {
        "id": 1,
        "course": 101,
        "course_title": "Python Backend Bootcamp",
        "invited_by": 2,
        "invited_by_name": "Sarah Williams",
        "invited_user": 5,
        "invited_user_name": "Alex Chen",
        "invited_user_email": "co.instructor@example.com",
        "token": "550e8400-e29b-41d4-a716-446655440000",
        "status": "pending",
        "expires_at": "2026-06-15T10:00:00Z",
        "responded_at": null,
        "created_at": "2026-06-08T10:00:00Z",
        "updated_at": "2026-06-08T10:00:00Z"
    }
}
```

The invitee receives an email with a **View Invitation** link pointing to `{FRONTEND_URL}/invites/{token}`.

### Error cases

| Scenario | Status | Message |
|---|---|---|
| Caller is not course owner | 403 | `"Only the course owner can send invites."` |
| Email not a verified instructor on the platform | 400 | `"No verified instructor found with this email."` |
| Inviting yourself | 400 | `"You cannot invite yourself."` |
| Already an instructor on the course | 400 | `"This user is already an instructor on this course."` |
| Pending invite already exists for this user | 400 | `"A pending invite already exists for this user."` |
| Course is not editable (published / under_review / archived) | 422 | `"This course is ... and cannot be edited."` |

---

## 34B. List Invites for a Course

**GET** `{{base_url}}/courses/{{course_id}}/instructors/invites/`

**Headers:** owner JWT.

Optional filter: `?status=pending` (accepted, declined, expired, revoked).

**Expected 200:**
```json
{
    "success": true,
    "data": [ { "...invite object..." } ]
}
```

### Error cases

| Scenario | Status | Message |
|---|---|---|
| Caller is not owner (co-instructor) | 403 | `"Only the course owner can view invites."` |
| Invalid `?status=` value | 400 | `"Invalid status. Choices: accepted, declined, expired, pending, revoked."` |

---

## 34C. Revoke an Invite

**DELETE** `{{base_url}}/courses/{{course_id}}/instructors/invites/{{invite_id}}/`

**Headers:** owner JWT.

**Body:** *(empty)*

**Expected 200:**
```json
{ "success": true, "message": "Invite revoked." }
```

### Error cases

| Scenario | Status | Message |
|---|---|---|
| Caller is not owner | 403 | `"Only the course owner can revoke invites."` |
| Invite not in pending status | 422 | `"Only pending invites can be revoked."` |

---

## 34D. My Received Invites

**GET** `{{base_url}}/courses/invites/my/`

**Headers:** instructor JWT (the invitee).

Defaults to `?status=pending`. Pass `?status=accepted` / `declined` / `expired` / `revoked` for history.

**Expected 200:**
```json
{
    "success": true,
    "data": [
        {
            "id": 1,
            "course": 101,
            "course_title": "Python Backend Bootcamp",
            "invited_by": 2,
            "invited_by_name": "Sarah Williams",
            "token": "550e8400-e29b-41d4-a716-446655440000",
            "status": "pending",
            "expires_at": "2026-06-15T10:00:00Z",
            "...": "..."
        }
    ]
}
```

---

## 34E. Accept an Invite

**POST** `{{base_url}}/courses/invites/{{token}}/accept/`

**Headers:** invitee instructor JWT.

**Body:** *(empty)*

**Expected 200:**
```json
{
    "success": true,
    "message": "You have joined \"Python Backend Bootcamp\" as a co-instructor.",
    "data": { "...invite object with status: accepted..." }
}
```

On success the caller is atomically added to `course.instructors`. The course is now accessible via `GET /courses/{{course_id}}/` for the new co-instructor.

### Error cases

| Scenario | Status | Message |
|---|---|---|
| Token not found or belongs to another user | 404 | `"Invite not found."` |
| Invite already accepted / declined / revoked / expired | 410 | `"This invite is no longer valid."` |
| Invite past `expires_at` (not yet swept by Celery) | 410 | `"This invite has expired."` |

---

## 34F. Decline an Invite

**POST** `{{base_url}}/courses/invites/{{token}}/decline/`

**Headers:** invitee instructor JWT.

**Body:** *(empty)*

**Expected 200:**
```json
{
    "success": true,
    "message": "You have declined the invitation to \"Python Backend Bootcamp\".",
    "data": { "...invite object with status: declined..." }
}
```

The record is kept (visible to owner via Section 34B with `?status=declined`). The owner can send a new invite to the same user after a decline.

---

# Public Catalog (No Auth)

## 35. Public Catalog

### 35.1 Browse the Catalog

**GET** `{{base_url}}/courses/catalog/`

No Authorization header needed.

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "count": 2,
        "next": null,
        "previous": null,
        "results": [
            {
                "id": 1,
                "title": "Python Backend Bootcamp",
                "slug": "python-backend-bootcamp",
                "description": "Build production APIs with Django and DRF.",
                "thumbnail": null,
                "price": "79.99",
                "language": "English",
                "level": "intermediate",
                "duration_minutes": 240,
                "instructors": [
                    { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" }
                ],
                "category": { "id": 1, "name": "Backend Development", "slug": "backend" },
                "published_at": "2026-05-10T09:00:00Z"
            }
        ]
    }
}
```

### 35.2 Filter the Catalog

The catalog supports multi-criteria filtering and sorting. All params are optional and combine with AND. Successful filtered responses follow the same `200` paginated shape as 35.1.

#### Quick reference — all supported params

**Filter fields** (all optional, AND-combined):

| Param | Type / accepted values | Example |
|---|---|---|
| `category` | slug (single) | `?category=programming` |
| `subcategory` | slug (single) | `?subcategory=python` |
| `level` | CSV of `beginner`, `intermediate`, `advanced` | `?level=beginner,intermediate` |
| `language` | CSV of language strings (case-insensitive) | `?language=english,bangla` |
| `price_type` | `free` or `paid` | `?price_type=free` |
| `price_min` | non-negative decimal | `?price_min=10` |
| `price_max` | non-negative decimal | `?price_max=99.99` |
| `duration_min` | non-negative integer (minutes) | `?duration_min=60` |
| `duration_max` | non-negative integer (minutes) | `?duration_max=240` |
| `search` | text — matches title, description, instructor full name | `?search=python` |
| `rating_min` | decimal 1.0–5.0 — minimum avg rating | `?rating_min=4.0` |
| `min_reviews` | integer ≥ 0 — minimum number of reviews | `?min_reviews=10` |
| `page` | page number (see Section 46) | `?page=2` |
| `page_size` | items per page, max 100 (see Section 46) | `?page_size=25` |

**Sort field** (`?sort=<key>` — one value at a time):

| Key | Effect |
|---|---|
| `relevance` | Title match rank desc, then newest. Default when `?search=` is present. |
| `newest` | `-published_at`. Default when no `?search=`. |
| `popularity` | Active enrollment count desc, then newest. |
| `price_asc` | Price ascending. |
| `price_desc` | Price descending. |
| `rating` | Average rating descending, then newest. Courses with no reviews sort last (`NULLS LAST`). |

#### 35.2.1 Category / subcategory

| Filter | Param | Example | Behavior |
|---|---|---|---|
| Top-level category | `?category=<slug>` | `?category=programming` | Matches that category **and** every subcategory under it. |
| Subcategory only | `?subcategory=<slug>` | `?subcategory=python` | Matches the exact subcategory. |
| Both (with validation) | `?category=<parent>&subcategory=<child>` | `?category=programming&subcategory=python` | Only returns rows whose category is the child **and** whose parent is the given category. Mismatched pairs (e.g. `?category=cooking&subcategory=python`) return an empty list. |

#### 35.2.2 Level (multi-select)

CSV — picks multiple levels at once. Accepted values: `beginner`, `intermediate`, `advanced`.

| Example | Result |
|---|---|
| `?level=intermediate` | Intermediate only |
| `?level=beginner,intermediate` | Beginner OR Intermediate |

#### 35.2.3 Language (multi-select, case-insensitive)

| Example | Result |
|---|---|
| `?language=English` | English courses |
| `?language=english,bangla` | English OR Bangla |

#### 35.2.4 Price

| Filter | Param | Example |
|---|---|---|
| Free only | `?price_type=free` | `{{base_url}}/courses/catalog/?price_type=free` |
| Paid only | `?price_type=paid` | `{{base_url}}/courses/catalog/?price_type=paid` |
| Minimum price | `?price_min=<decimal>` | `?price_min=10` |
| Maximum price | `?price_max=<decimal>` | `?price_max=99.99` |
| Price range | both bounds | `?price_min=10&price_max=99.99` |

`price_min` and `price_max` must be non-negative. Negative values return 400.

#### 35.2.5 Duration (in minutes)

| Filter | Param | Example | Notes |
|---|---|---|---|
| Minimum duration | `?duration_min=<int>` | `?duration_min=60` | 1+ hour |
| Maximum duration | `?duration_max=<int>` | `?duration_max=240` | ≤ 4 hours |
| Duration range | both bounds | `?duration_min=60&duration_max=240` | 1–4 hours |

Frontends rendering "Hours / Weeks / Months" sliders convert to minutes before sending. Values must be non-negative integers.

#### 35.2.6 Search

`?search=<text>` matches against **title**, **description**, and **instructor full name** (case-insensitive substring).

| Example | What matches |
|---|---|
| `?search=python` | Any course with "python" in the title or description, or any course taught by an instructor whose name contains "python". |
| `?search=sarah%20chen` | Courses by instructors named "Sarah Chen". |

#### 35.2.7 Combining filters

All params AND together. Example: beginner courses in the `programming-python` subcategory, under $50, taught in English, ordered by popularity:

```
{{base_url}}/courses/catalog/?category=programming&subcategory=python&level=beginner&price_max=50&language=english&sort=popularity
```

#### 35.2.8 Filter validation errors

Invalid filter values return 400 with a field-keyed `errors` object so the frontend can highlight every bad input at once.

| Bad input | Status | Sample error |
|---|---|---|
| `?sort=cheapest` | 400 | `{"sort": ["Invalid sort \"cheapest\". Must be one of: newest, popularity, price_asc, price_desc, rating, relevance."]}` |
| `?level=expert` | 400 | `{"level": ["Invalid level(s): expert. Must be one of: advanced, beginner, intermediate."]}` |
| `?level=beginner,foobar` | 400 | `{"level": ["Invalid level(s): foobar. Must be one of: advanced, beginner, intermediate."]}` |
| `?price_min=-10` | 400 | `{"price_min": ["Must be non-negative."]}` |
| `?price_max=abc` | 400 | `{"price_max": ["\"abc\" is not a valid number."]}` |
| `?duration_max=3.5` | 400 | `{"duration_max": ["\"3.5\" is not a valid integer."]}` |
| `?duration_min=-5` | 400 | `{"duration_min": ["Must be non-negative."]}` |

Multiple bad fields are reported in one response:

```http
GET {{base_url}}/courses/catalog/?sort=foo&level=wizard&price_min=-1
```
```json
{
    "success": false,
    "message": "Invalid filter parameters.",
    "errors": {
        "sort": ["Invalid sort \"foo\". Must be one of: newest, popularity, price_asc, price_desc, rating, relevance."],
        "level": ["Invalid level(s): wizard. Must be one of: advanced, beginner, intermediate."],
        "price_min": ["Must be non-negative."]
    }
}
```

### 35.3 View a Single Course Detail (Catalog)

**GET** `{{base_url}}/courses/catalog/{{course_slug}}/`

No Authorization header needed.

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "id": 1,
        "title": "Python Backend Bootcamp",
        "slug": "python-backend-bootcamp",
        "description": "Build production APIs with Django and DRF.",
        "thumbnail": null,
        "price": "79.99",
        "language": "English",
        "level": "intermediate",
        "duration_minutes": 240,
        "instructors": [
            { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" }
        ],
        "partner_institution": null,
        "category": { "id": 1, "name": "Backend Development", "slug": "backend" },
        "learning_objectives": "Build REST APIs with Django REST Framework.",
        "prerequisites": "Basic Python knowledge.",
        "audiences": "Developers who want to build backend APIs.",
        "total_sections": 5,
        "total_content_items": 20,
        "published_at": "2026-05-10T09:00:00Z"
    }
}
```

**Error — course not found or not published:** 404 — `{ "detail": "No NidusCourse matches the given query." }`

---

## 35A. Course Categories

Categories are a 2-level taxonomy (`parent` → `children`). The **list is public**;
**create / update / delete are admin-only** (`IsPlatformAdmin` — a staff or
`user_type=admin` account). Slug is auto-generated from `name` when omitted.
Deleting soft-deactivates (`is_active=False`); the row and any course FKs remain.

### 35A.1 List Categories (public, nested tree, paginated)

**GET** `{{base_url}}/courses/categories/`

No Authorization header needed. Returns active, top-level categories sorted
alphabetically by `name`, each with its active children nested (also alphabetical).
Paginated (page size 10, `?page=` / `?page_size=` up to 100).

**Expected 200:**
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
                "name": "Programming",
                "slug": "programming",
                "children": [
                    {
                        "id": 2,
                        "name": "Python",
                        "slug": "python",
                        "children": []
                    }
                ]
            }
        ]
    }
}
```

### 35A.2 Create Category (admin)

**POST** `{{base_url}}/courses/categories/`

**Headers:** `Authorization: Bearer {{admin_token}}`

```json
{ "name": "Data Science" }
```

**Expected 201:**
```json
{
    "success": true,
    "message": "Category created.",
    "data": {
        "id": 3,
        "name": "Data Science",
        "slug": "data-science",
        "parent": null,
        "is_active": true,
        "created_at": "...",
        "updated_at": "..."
    }
}
```

Save `data.id` as `{{category_id}}`. `slug` is optional — pass one explicitly to
override the auto value.

**Create a subcategory** (pass a top-level `parent` id):
```json
{ "name": "Machine Learning", "parent": 3 }
```

### 35A.3 Get Category Detail (admin)

**GET** `{{base_url}}/courses/categories/{{category_id}}/`

**Headers:** `Authorization: Bearer {{admin_token}}`

Returns the full record (all fields, including inactive rows).

### 35A.4 Update Category (admin)

**PATCH** `{{base_url}}/courses/categories/{{category_id}}/`

```json
{ "name": "Data & AI" }
```

**Expected 200:** `"message": "Category updated."` with the updated record.

Deactivate (hides from the public list) by patching `is_active`:
```json
{ "is_active": false }
```

### 35A.5 Delete Category (admin, soft)

**DELETE** `{{base_url}}/courses/categories/{{category_id}}/`

**Expected 200:**
```json
{ "success": true, "message": "Category deactivated." }
```

The row still exists with `is_active=false`; courses keep their `category` FK.

### 35A.6 Validation & Error Cases

| Scenario | Status | Detail |
|---|---|---|
| Empty / whitespace `name` | 400 | `errors.name: ["Name cannot be empty."]` |
| Parent is not top-level (3-level attempt) | 400 | `errors.parent: ["Categories support only two levels; the parent must be a top-level category."]` |
| Self-parent on update | 400 | `errors.parent: ["A category cannot be its own parent."]` |
| Duplicate `name` / `slug` | 422 | `"A category with this name or slug already exists."` |
| Non-admin create / update / delete | 403 | `"Only administrators can perform this action."` |
| Unknown `pk` on detail | 404 | `"Category not found."` |

> Catalog filtering consumes these: `?category=<slug>` rolls a parent down to its
> children, `?subcategory=<slug>` matches exactly. See Section 35.

---

# Learner — Enrollment & Dashboard

> All endpoints in Sections 36–37 require `Authorization: Bearer {{access_token}}` and a **learner** account with a **verified email** (unless a course's own instructor is calling the my-courses detail endpoint for preview).

## 36. Enrollment

### 36.1 Enroll in a Course

**POST** `{{base_url}}/courses/{{course_slug}}/enroll/`

**Body:** *(empty)*

**Expected 201:**
```json
{
    "success": true,
    "message": "Enrolled successfully.",
    "data": {
        "id": 10,
        "course": {
            "id": 1,
            "title": "Python Backend Bootcamp",
            "slug": "python-backend-bootcamp",
            "description": "Build production APIs with Django and DRF.",
            "thumbnail": null,
            "price": "79.99",
            "language": "English",
            "level": "intermediate",
            "duration_minutes": 240,
            "instructors": [
                { "id": 2, "full_name": "Jane Smith", "email": "jane@example.com" }
            ],
            "category": { "id": 1, "name": "Backend Development", "slug": "backend" },
            "published_at": "2026-05-10T09:00:00Z"
        },
        "enrollment_type": "free",
        "is_active": true,
        "progress_percent": 0,
        "completed_at": null,
        "last_accessed_at": null,
        "created_at": "2026-05-13T10:30:00Z"
    }
}
```

> Paid courses also enroll as `enrollment_type: "free"` until the payment integration is added.

### 36.2 Unenroll from a Course

**POST** `{{base_url}}/courses/{{course_slug}}/unenroll/`

**Body:** *(empty)*

**Expected 200:**
```json
{
    "success": true,
    "message": "Unenrolled successfully. Your progress has been preserved.",
    "data": {
        "id": 10,
        "course": { "...": "same course object as above" },
        "enrollment_type": "free",
        "is_active": false,
        "progress_percent": 0,
        "completed_at": null,
        "last_accessed_at": null,
        "created_at": "2026-05-13T10:30:00Z"
    }
}
```

Notice `is_active` flips to `false`. Progress is preserved — re-enrolling reactivates the same row.

### 36.3 Re-enroll After Unenrolling

**POST** `{{base_url}}/courses/{{course_slug}}/enroll/`

Same response shape as 36.1, with `is_active: true` again. The enrollment `id` stays the same — no duplicate row is created.

### 36.4 Enrollment Error Cases

| Scenario | Status | Body |
|---|---|---|
| Enroll twice while already enrolled | 422 | `{"success": false, "message": "You are already enrolled in this course."}` |
| Unenroll without ever enrolling | 422 | `{"success": false, "message": "You are not currently enrolled in this course."}` |
| Non-learner (instructor) tries to enroll | 403 | `{"detail": "Only learners can access this resource."}` |
| Unverified learner | 403 | `{"detail": "Email address is not verified."}` |
| Enroll in a non-published course (draft/rejected slug) | 404 | `{"detail": "No NidusCourse matches the given query."}` |
| Unauthenticated access to a protected endpoint | 401 | `{"detail": "Authentication credentials were not provided."}` |

---

## 37. My Courses

The `/my-courses/` family is for the learner's dashboard and the course-player page header. The course-player UI composes its full page from three calls: `/my-courses/<slug>/` (header card with metadata + overall progress), `/learn/<slug>/curriculum/` (sidebar), and `/learn/<thing>/<id>/` (the item the learner clicked).

### 37.1 List My Enrollments (Dashboard)

**GET** `{{base_url}}/courses/my-courses/`

**Headers:** enrolled learner JWT.

**Expected 200 (paginated):**
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
                    "instructors": [],
                    "category": {},
                    "published_at": "..."
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

Ordered by `last_accessed_at` (most recent first), then `created_at`. Only the caller's own active enrollments are returned.

### 37.2 Get My-Course Detail (Player Header)

**GET** `{{base_url}}/courses/my-courses/{{course_slug}}/`

**Headers:** enrolled learner OR course's instructor JWT.

**Expected 200:**
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
            "instructors": [],
            "partner_institution": null,
            "category": {},
            "learning_objectives": "",
            "prerequisites": "",
            "audiences": "",
            "total_sections": 12,
            "total_content_items": 47
        }
    }
}
```

Notes:
- This response does **not** include the curriculum tree. Fetch `/learn/{{course_slug}}/curriculum/` for the sidebar.
- `is_instructor: true` is returned when the caller is one of the course's instructors (preview mode). In that case `enrollment` is `null`.
- Each GET updates the learner's `last_accessed_at` timestamp on the enrollment row (debounced — see Section 46).

### 37.3 My-Courses Detail Error Cases

| Scenario | Status | Message |
|---|---|---|
| Unenrolled learner | 403 | `"You do not have access to this course."` |
| Course slug not found | 404 | (default DRF 404) |
| Unauthenticated | 401 | (default DRF 401) |

---

# Learner Consumption (`/learn/...`)

> All `/learn/...` endpoints require a verified-email JWT. `GET` endpoints accept either an enrolled learner or the course's own instructor (preview). `POST /progress/`, `POST /submit/`, and `POST /retry/` are learner-only — instructors get 403.

## 38. Curriculum Outline

### 38.1 Get Learner Curriculum

**GET** `{{base_url}}/courses/learn/{{course_slug}}/curriculum/`

**Headers:** enrolled learner OR course's instructor JWT.

**Expected 200:**
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

Notes:
- `is_completed` appears only for learners; instructors previewing get the same payload without that key.
- Heavy item payloads (HLS URLs, quiz questions, article text, coding configs) are not in this response — fetch them from per-item endpoints.

### 38.2 Curriculum Error Cases

| Scenario | Status | Body |
|---|---|---|
| Unenrolled learner | 403 | `{"success": false, "message": "You do not have access to this course."}` |
| Course slug not found | 404 | — |
| Unauthenticated | 401 | — |

---

## 39. Lecture Detail + Watch Progress

### 39.1 Get Learner Lecture Detail

**GET** `{{base_url}}/courses/learn/lectures/{{lecture_id}}/`

**Headers:** enrolled learner OR course's instructor JWT.

**Video lecture (learner caller):**
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

**Article lecture:**
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

Notes:
- `progress` is `null` for the instructor preview caller (no per-instructor watch history).
- `transcoding_error` is intentionally not exposed.

### 39.2 Lecture Detail Error Cases

| Scenario | Status | Body |
|---|---|---|
| Unenrolled learner | 404 | `{"success": false, "message": "Lecture not found."}` (existence not leaked) |
| Lecture not found | 404 | same |

### 39.3 Upsert Watch Progress

**POST** `{{base_url}}/courses/learn/lectures/{{lecture_id}}/progress/`

**Headers:** enrolled learner JWT (instructors get 403).

```json
{ "watched_seconds": 120, "is_completed": false }
```

**Expected 200:**
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

Notes:
- Idempotent — repeated POSTs with the same body never create duplicate `WatchProgress` rows.
- `watched_seconds` is server-clamped to the active video's `duration_seconds`. Sending `99999` for a 600-second video stores `600`, not `99999`.
- If the clamped cursor lands at duration, the server forces `is_completed: true` regardless of what the client sent. The response reflects the corrected values.
- Article lectures have no duration; `watched_seconds` is forced to `0` on save.
- When `is_completed` flips, a signal recalculates the enrollment's `progress_percent`. Re-fetch `/my-courses/` to see the updated rollup.

### 39.4 Progress Endpoint Error Cases

| Scenario | Status | Body |
|---|---|---|
| Negative `watched_seconds` | 400 | `errors.watched_seconds: ["Ensure this value is greater than or equal to 0."]` |
| Missing `is_completed` | 400 | `errors.is_completed: ["This field is required."]` |
| Unenrolled learner | 404 | (existence not leaked) |
| Instructor calling progress endpoint | 403 | `"Only learners can access this resource."` |

---

## 40. Quiz Detail + Submission

### 40.1 Get Learner Quiz Detail (Attempt UI)

**GET** `{{base_url}}/courses/learn/quizzes/{{quiz_id}}/`

**Headers:** enrolled learner OR course's instructor JWT.

**Expected 200:**
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

Notes:
- Each answer option carries only `id` + `answer_text` — `is_correct` is **never** in this payload.
- `latest_attempt` is `null` if the caller has never submitted (or is an instructor previewing).
- Instructor preview is allowed (no `is_correct` leak even to them).

### 40.2 Submit a Quiz Attempt

**POST** `{{base_url}}/courses/learn/quizzes/{{quiz_id}}/submit/`

**Headers:** enrolled learner JWT (instructors get 403).

```json
{
    "answers": [
        { "question_id": 1, "selected_answer_id": 6 },
        { "question_id": 2, "selected_answer_id": 9 },
        { "question_id": 3, "selected_answer_id": null }
    ]
}
```

**Expected 200:** Score + per-question verdict. `correct_answer_id` / `correct_answer_text` appear **only when `is_correct=false`**:
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

Notes:
- Each POST creates a **new** `QuizAttempt` row — repeated submits don't overwrite past attempts.
- Unanswered questions (`selected_answer_id: null`) score as wrong and reveal the correct answer.
- Each successful submit recalculates `enrollment.progress_percent` (a quiz counts as complete once the learner has ≥1 `QuizAttempt` row for it).

### 40.3 Quiz Submission Error Cases

| Scenario | Status | Body |
|---|---|---|
| `question_id` not in this quiz | 400 | `errors.answers: ["question_id 99 does not belong to this quiz."]` |
| `selected_answer_id` not under cited question | 400 | `errors.answers: ["selected_answer_id 22 does not belong to question 1."]` |
| Duplicate `question_id` in payload | 400 | — |
| Unenrolled learner | 404 | (existence not leaked) |
| Instructor calling submit | 403 | — |

---

## 41. Assignment Detail + Submission

### 41.1 Get Learner Assignment Detail

**GET** `{{base_url}}/courses/learn/assignments/{{assignment_id}}/`

**Headers:** enrolled learner JWT (instructor preview also allowed).

**Expected 200:**
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
- `model_answer` and `rubric` are **never** present in this payload.
- `latest_submission` summarizes the caller's most recent submission (`submission_id`, `status`, `total_score`, `max_score`, `submitted_at`, `graded_at`).

### 41.2 Submit an Assignment (auto-graded)

**POST** `{{base_url}}/courses/learn/assignments/{{assignment_id}}/submit/`

**Headers:** enrolled learner JWT.

```json
{
    "answers": [
        {"question_id": 1, "answer_text": "Idempotency means PUT and DELETE are safe to retry without compounding side effects, unlike POST. That guides retry policy at the gateway."},
        {"question_id": 2, "answer_text": "Idempotent verbs let the client retry on network failure without worrying about duplicate state changes."}
    ]
}
```

**Expected 202 Accepted:**
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
- The response returns immediately with `status='submitted'`. A Celery task (`grade_assignment_submission_task`) runs the rubric grader out-of-band. Poll `GET /learn/assignments/submissions/{submission_id}/` until `status` transitions to `passed`, `failed`, or `grading_failed`.
- `max_score` is snapshotted at submit time. Even if the instructor later edits `AssignmentQuestion.points`, the submission's max stays frozen.
- All questions on the assignment must appear in the `answers` array (use `""` for a deliberately-blank answer). Missing a question → 400.

### 41.3 Get Learner Submission Detail (polling target)

**GET** `{{base_url}}/courses/learn/assignments/submissions/{{submission_id}}/`

**Headers:** the same learner that submitted (other learners → 404).

**While grading:**
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

**Once graded (`status='passed'` or `'failed'`):**
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
                    {"index": 0, "type": "keyword", "matched": true, "points_awarded": 4, "feedback": "Correctly identifies idempotency."},
                    {"index": 1, "type": "any_of", "matched": true, "points_awarded": 2, "feedback": "Mentions an HTTP verb."}
                ],
                "feedback": "Correctly identifies idempotency.\nMentions an HTTP verb.",
                "model_answer": "Reference reflection: idempotency boundaries, ..."
            }
        ]
    }
}
```

**Reveal rule (verify in tests):** `model_answer` is **omitted entirely** when `status in ('submitted', 'grading', 'grading_failed')`. It is **included** only when `status in ('passed', 'failed')`.

**Polling pattern:**
1. After `POST /submit/` returns 202, poll `GET /learn/assignments/submissions/{submission_id}/` every 2–5 seconds.
2. Stop polling once `status` is one of `passed`, `failed`, or `grading_failed`.
3. If terminal status is `grading_failed`, show the `grading_error` and offer the retry button (41.4).

### 41.4 Retry a Failed Grading

**POST** `{{base_url}}/courses/learn/assignments/submissions/{{submission_id}}/retry/`

**Headers:** the same learner that owns the submission.
**Body:** *(empty)*

**Expected 202** (when the prior status was `grading_failed`):
```json
{
    "success": true,
    "message": "Grading re-enqueued.",
    "data": { "submission_id": 7, "status": "grading" }
}
```

Notes:
- The same submission row is reused — `submitted_at` is unchanged, `grading_error` is cleared, `status` flips to `grading`, and the Celery task is re-dispatched.
- Only `grading_failed` is retryable. Any other status → 422 with `"Only submissions in grading_failed can be retried."`.
- A submission owned by a different learner → 404 (existence not leaked).
- For a fresh attempt after a graded `failed`/`passed`, use `POST /submit/` to create a new submission row — that's distinct from `/retry/`.

### 41.5 Assignment Submission Error Cases

| Scenario | Status | Body |
|---|---|---|
| In-flight submission already exists (`status in ('submitted', 'grading')`) | 422 | `"You already have a submission for this assignment that is still being graded."` |
| `question_id` not in assignment | 400 | `errors.answers` lists offending ids |
| Duplicate `question_id` in payload | 400 | — |
| Missing some questions in payload | 400 | `errors.answers` — all-or-nothing rule |
| Unenrolled learner | 404 | (existence not leaked) |
| Instructor calling `/submit/` or `/retry/` | 403 | preview must not pollute submission history |
| Submission detail for another learner's submission | 404 | — |

### Resubmission UX (frontend responsibility)

After a `failed` or `grading_failed` verdict the learner can retry the grader (41.4) **or** submit fresh answers via `POST /submit/`. The frontend should fetch the most recent submission detail and render its `criterion_results` next to the new submission form — otherwise the learner has no idea which criteria they missed last time.

---

## 42. Coding Exercise Detail + Run + Submit

The learner side of coding exercises follows the architecture documented in
[docs/architecture/09-coding-exercises.md](docs/architecture/09-coding-exercises.md). There are **two**
execution paths with different persistence semantics:

| Mode | Endpoint | Persisted? | Test cases run | Returns | Poll via |
|---|---|---|---|---|---|
| **Run** | `POST /learn/coding-exercises/<id>/run/` | No — Celery result only (expires in 1 h) | Visible only (`is_hidden=false`) | `{task_id}` (HTTP 202) | `GET /learn/coding-exercises/tasks/<task_id>/` |
| **Submit** | `POST /learn/coding-exercises/<id>/submit/` | Yes — `CodingSubmission` row | All (visible + hidden) | Queued submission (HTTP 202) | `GET /learn/coding-exercises/submissions/<id>/` |

Run is the IDE "try it" button — cheap, ephemeral, hides nothing the learner could not already see. Submit is the graded attempt; every test case (including hidden) is evaluated and the row in `CodingSubmission` is the permanent record used for `is_solved` and progress calculations.

Hidden test cases are filtered at the task layer for Run, and **omitted from `test_results` entirely at the serializer layer for Submit** — the learner only sees rows for visible tests. Aggregate counts (`total_tests` / `passed_tests` / `score`) still include hidden tests so the learner can tell whether they passed overall, but no per-hidden-row data is exposed. `solution_code` is never present on any learner-facing response.

Execution runs inside a Docker sandbox (`network_disabled=True`, 128 MB RAM, 0.5 CPU, read-only FS, 32 MB tmpfs at `/tmp`, all caps dropped). One container per submission runs every test in a batched harness — see the plan doc for the optimisation rationale.

### 42.1 Get Learner Coding Exercise Detail

**GET** `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/`

**Headers:** enrolled learner JWT (instructor preview also allowed).

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "id": 1,
        "section_id": 11,
        "title": "Sum Two",
        "description": "Sum the two integers in input.",
        "problem_statement": "Given two ints on one line, print their sum.",
        "difficulty": "easy",
        "default_language": "python",
        "supported_languages": ["python", "javascript"],
        "time_limit_ms": 2000,
        "language_configs": [
            {
                "id": 1,
                "language": "python",
                "starter_code": "def solve(s):\n    pass\n"
            }
        ],
        "test_cases": [
            {
                "id": 1,
                "input_data": "1 2",
                "expected_output": "3",
                "explanation": "easy",
                "position": 1
            },
            {
                "id": 2,
                "input_data": "4 5",
                "expected_output": "9",
                "explanation": "",
                "position": 2
            }
        ],
        "latest_submission": null
    }
}
```

Notes:
- `solution_code` is **never** present on `language_configs[*]`. The field is intentionally not declared on the learner serializer (not conditionally stripped) — absence is a stronger guarantee than conditional removal.
- Hidden test cases are filtered out of `test_cases`. Only `is_hidden=false` rows are returned. The `is_hidden` field itself isn't declared on the learner test-case serializer, so the learner cannot infer hidden-test count from this payload.
- `latest_submission` summarises the caller's most recent submission with `id`, `status`, `score`, `passed_tests`, `total_tests`, `runtime_ms`, `submitted_at`, `completed_at`. `null` when the caller has never submitted or is an instructor previewing.

### 42.2 Run Code (Transient, Visible Tests Only)

**POST** `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/run/`

**Headers:** enrolled learner JWT. Instructors get 403 (preview must not pollute history).

```json
{
    "language": "python",
    "code": "def solve(s):\n    a, b = map(int, s.split())\n    return a + b\n"
}
```

**Expected 202 Accepted:**
```json
{
    "success": true,
    "message": "Run dispatched.",
    "data": { "task_id": "e2aa9e54-4a62-48d7-a86b-d599573a462d" }
}
```

Notes:
- Only the **visible** test cases are executed (`is_hidden=false`). The result dict the polling endpoint returns therefore only contains visible-test data — there is no per-row redaction layer needed because hidden tests never enter the Run pipeline.
- The Celery result lives in Redis and expires after `CELERY_RESULT_EXPIRES = 3600` seconds. A `task_id` polled after one hour returns `PENDING` forever — frontend polling should give up after a sensible window.
- Run does NOT create a `CodingSubmission` row, does NOT update `progress_percent`, does NOT count toward "solved".
- `language` must be one of the exercise's `supported_languages` AND one of `{python, javascript, cpp, java}`. Mismatch → 400.

### 42.3 Poll a Run Task

**GET** `{{base_url}}/courses/learn/coding-exercises/tasks/{{task_id}}/`

**Headers:** any verified-email JWT. Task IDs are unguessable UUIDs and the result payload contains only visible test data.

**While pending:**
```json
{ "success": true, "data": { "state": "PENDING" } }
```

**Started (worker has the task):**
```json
{ "success": true, "data": { "state": "STARTED" } }
```

**Success:**
```json
{
    "success": true,
    "data": {
        "state": "SUCCESS",
        "result": {
            "exercise_id": 1,
            "language": "python",
            "status": "passed",
            "total_tests": 2,
            "passed_tests": 2,
            "score": 100.0,
            "runtime_ms": 4,
            "error_message": "",
            "test_results": [
                {
                    "position": 1,
                    "input_data": "1 2",
                    "expected_output": "3",
                    "actual_output": "3",
                    "stdout": "3",
                    "stderr": "",
                    "status": "passed",
                    "runtime_ms": 2,
                    "exit_code": 0
                },
                {
                    "position": 2,
                    "input_data": "4 5",
                    "expected_output": "9",
                    "actual_output": "9",
                    "stdout": "9",
                    "stderr": "",
                    "status": "passed",
                    "runtime_ms": 2,
                    "exit_code": 0
                }
            ]
        }
    }
}
```

**Infrastructure failure (e.g. Docker daemon unreachable):**
The `state` is still `SUCCESS` (the task itself didn't crash — it just couldn't reach the runner) and `result.status` is `error` with a populated `error_message`. The frontend should render this as a runner-side problem, not a learner-code problem.

**Task crashed:**
```json
{
    "success": false,
    "message": "Run failed.",
    "data": { "state": "FAILURE", "error": "<exception repr>" }
}
```
Returns HTTP 500.

**Polling pattern:**
1. After `POST /run/` returns 202, poll `GET /learn/coding-exercises/tasks/<task_id>/` every 500 ms.
2. Stop polling when `state` is `SUCCESS` or `FAILURE`.
3. Cap the total polling window at ~60 s — past `CELERY_RESULT_EXPIRES = 3600` s the task ID becomes indistinguishable from a non-existent one.

### 42.4 Submit Code (Persisted, All Tests)

**POST** `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/submit/`

**Headers:** enrolled learner JWT.

```json
{
    "language": "python",
    "code": "def solve(s):\n    a, b = map(int, s.split())\n    return a + b\n"
}
```

**Expected 202 Accepted:**
```json
{
    "success": true,
    "message": "Submission queued.",
    "data": {
        "id": 7,
        "exercise_id": 1,
        "language": "python",
        "code": "def solve(s):\n    a, b = map(int, s.split())\n    return a + b\n",
        "status": "queued",
        "total_tests": 3,
        "passed_tests": 0,
        "score": "0.00",
        "runtime_ms": 0,
        "error_message": "",
        "stdout": "",
        "stderr": "",
        "submitted_at": "2026-05-23T11:42:18.301Z",
        "completed_at": null,
        "test_results": []
    }
}
```

Notes:
- The response returns immediately with `status='queued'`. A Celery task (`evaluate_coding_submission_task`) runs all test cases inside a single Docker container, then updates the row in place. Poll `GET /learn/coding-exercises/submissions/<id>/` until `status` transitions to one of `passed` / `failed` / `error`.
- `total_tests` is snapshotted at submit time from `exercise.test_cases.count()`. Even if the instructor later adds or removes test cases, this submission's denominator stays frozen.
- The submission row is created before the task is dispatched (`transaction.on_commit`), so the frontend always has an `id` to poll.

### 42.5 Get Submission Detail (Polling Target)

**GET** `{{base_url}}/courses/learn/coding-exercises/submissions/{{submission_id}}/`

**Headers:** the same learner that submitted (other learners → 404).

**While queued / grading:**
```json
{
    "success": true,
    "data": {
        "id": 7,
        "exercise_id": 1,
        "language": "python",
        "code": "...",
        "status": "grading",
        "total_tests": 3,
        "passed_tests": 0,
        "score": "0.00",
        "runtime_ms": 0,
        "error_message": "",
        "stdout": "",
        "stderr": "",
        "submitted_at": "2026-05-23T11:42:18.301Z",
        "completed_at": null,
        "test_results": []
    }
}
```

**Once terminal (`status` in `passed` / `failed` / `error`):**
```json
{
    "success": true,
    "data": {
        "id": 7,
        "exercise_id": 1,
        "language": "python",
        "status": "passed",
        "total_tests": 3,
        "passed_tests": 3,
        "score": "100.00",
        "runtime_ms": 15,
        "error_message": "",
        "stdout": "3\n9\n300",
        "stderr": "",
        "submitted_at": "2026-05-23T11:42:18.301Z",
        "completed_at": "2026-05-23T11:42:19.522Z",
        "test_results": [
            {
                "id": 1,
                "position": 1,
                "status": "passed",
                "runtime_ms": 5,
                "exit_code": 0,
                "is_hidden": false,
                "input_data": "1 2",
                "expected_output": "3",
                "actual_output": "3",
                "stdout": "3",
                "stderr": ""
            },
            {
                "id": 2,
                "position": 2,
                "status": "passed",
                "runtime_ms": 5,
                "exit_code": 0,
                "is_hidden": false,
                "input_data": "4 5",
                "expected_output": "9",
                "actual_output": "9",
                "stdout": "9",
                "stderr": ""
            }
        ]
    }
}
```

**Hidden-test exposure rules:**
- Hidden test rows (`is_hidden: true`) are **omitted entirely** from `test_results`. The learner cannot see their position, status, runtime, or any input/output for them.
- Aggregate fields (`total_tests`, `passed_tests`, `score`, `runtime_ms`) **do** include hidden tests. In the example above, an exercise with 1 visible + 2 hidden tests would report `total_tests: 3` with one row in `test_results`. The learner infers hidden-test pass/fail from the mismatch between `len(test_results)` and `total_tests` vs `passed_tests`.
- `solution_code` is never exposed anywhere on this endpoint.

**Status precedence on a Submit verdict:**
`error` > `failed` > `passed`. If any test errors, the submission status is `error`; else if any test fails, `failed`; else `passed`.

**`progress_percent` integration:** A submission that reaches `status='passed'` schedules `recalculate_progress` via `transaction.on_commit` inside the task. Distinct PASSED exercises count once toward completion (multiple passing attempts on the same exercise don't double-count). Re-fetch `/my-courses/` to see the rolled-up percentage.

**Polling pattern:**
1. After `POST /submit/` returns 202, poll `GET /learn/coding-exercises/submissions/<id>/` every 500–1000 ms.
2. Stop polling once `status` is `passed`, `failed`, or `error`.
3. If terminal status is `error`, show `error_message` and offer the retry button (42.6).

### 42.6 Retry an Errored Submission

**POST** `{{base_url}}/courses/learn/coding-exercises/submissions/{{submission_id}}/retry/`

**Headers:** the same learner that owns the submission.
**Body:** *(empty)*

**Expected 202** (when the prior status was `error`):
```json
{
    "success": true,
    "message": "Submission re-enqueued.",
    "data": { "submission_id": 7, "status": "queued" }
}
```

Notes:
- The same submission row is reused — `submitted_at` is unchanged, `error_message` is cleared, `status` flips back to `queued`, and the Celery task is re-dispatched on commit.
- Only `error` is retryable. `passed` / `failed` → 422 with `"Only submissions in error state can be retried."`. (For a fresh attempt after a `failed`/`passed`, use `POST /submit/` to create a new row.)
- Another learner's submission → 404 (existence not leaked).
- The retry endpoint is useful when an `error` was caused by a transient Docker/infrastructure issue, not a learner-code bug. Wholly broken code should be fixed and resubmitted, not retried.

### 42.7 Coding Submission Error Cases

| Scenario | Status | Body |
|---|---|---|
| In-flight submission already exists (`queued` / `grading`) | 422 | `"You already have a submission for this exercise that is still being graded."` |
| Empty `code` | 400 | `"Code cannot be empty."` |
| Language not in `supported_languages` for the exercise | 400 | `"Language '<lang>' is not configured for this exercise."` |
| Language outside `{python, javascript, cpp, java}` | 400 | `"Unsupported language: <lang>"` |
| Missing `language` or `code` field | 400 | `errors.language` / `errors.code` |
| Unenrolled learner | 404 | (existence not leaked) |
| Instructor calling `/run/`, `/submit/`, or `/retry/` | 403 | preview must not pollute submission history |
| Submission detail for another learner's submission | 404 | — |
| Retry on a non-error submission | 422 | `"Only submissions in error state can be retried."` |

### 42.8 Zombie Reaper (Operational)

A Celery beat task (`reap_stuck_coding_submissions_task`, schedule = 60 s) flips any `CodingSubmission` whose `status` is `queued` or `grading` for more than 5 minutes to `status='error'` with `error_message='Reaped: worker crashed or runner stalled.'`. This protects polling UIs from hanging on a row whose worker died mid-task.

This is **not** an API endpoint — it runs in the Celery beat process. To exercise it manually:

```bash
celery -A career_college_backend beat --loglevel=info
```

…or invoke the task directly from the Django shell:

```python
from courses.tasks import reap_stuck_coding_submissions_task
reap_stuck_coding_submissions_task.run()
# -> {'reaped': N}
```

---

# Certificates

## 43. Certificates

> Certificates are issued automatically when a learner's `progress_percent` reaches 100%. The endpoints below let learners retrieve their own certificate, share a public verification link, and download a PDF.

### 43.1 Get My Certificate (Learner)

**GET** `{{base_url}}/courses/my-courses/{{course_slug}}/certificate/`

**Headers:** learner JWT.

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "certificate_uid": "550e8400-e29b-41d4-a716-446655440000",
        "learner_name": "John Doe",
        "course_title": "Python Backend Bootcamp",
        "issued_at": "2026-06-13T12:00:00Z"
    }
}
```

| Scenario | Status | Body |
|---|---|---|
| Course not found / not published | 404 | `"Course not found."` |
| Enrolled but not completed | 404 | `"Certificate not yet issued."` |
| Not enrolled | 403 | (slug → 403, project policy) |

Save `certificate_uid` from the response as the `{{certificate_uid}}` variable for sections 43.2 and 43.3.

---

### 43.2 Verify Certificate (Public)

**GET** `{{base_url}}/courses/certificates/{{certificate_uid}}/verify/`

No Authorization header needed.

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "certificate_uid": "550e8400-e29b-41d4-a716-446655440000",
        "learner_name": "John Doe",
        "course_title": "Python Backend Bootcamp",
        "issued_at": "2026-06-13T12:00:00Z",
        "is_valid": true
    }
}
```

`is_valid` is always `true` if the row exists — there is no revocation mechanism.

| Scenario | Status |
|---|---|
| UUID not found | 404 |

---

### 43.3 Download Certificate PDF (Public)

**GET** `{{base_url}}/courses/certificates/{{certificate_uid}}/download/`

No Authorization header needed.

**Expected 200:** `Content-Type: application/pdf`, `Content-Disposition: attachment; filename="certificate-<uid>.pdf"`.

The PDF is generated on-the-fly from immutable database snapshots (reportlab). No file is stored on disk.

| Scenario | Status |
|---|---|
| UUID not found | 404 JSON |
| PDF generation error | 500 JSON |

---

# Course Reviews & Ratings

## 44. Course Reviews & Ratings

> Enrolled learners can submit, edit, and delete a review. Guests and unenrolled users can read reviews. Learners can vote on others' reviews. Aggregate stats are returned by the summary endpoint. All slug-based routes return 404 when the course is not found; the numeric `review_id` vote endpoint returns 404 on no-access (ID is not public-enumerable).

### 44.1 Get Review Summary (Public)

**GET** `{{base_url}}/courses/{{course_slug}}/reviews/summary/`

No Authorization header needed.

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "avg_rating": 4.32,
        "review_count": 87,
        "distribution": {
            "1": 2,
            "2": 5,
            "3": 10,
            "4": 25,
            "5": 45
        }
    }
}
```

---

### 44.2 List Reviews (Public, Paginated)

**GET** `{{base_url}}/courses/{{course_slug}}/reviews/`

No Authorization header needed. Optional query params:

| Param | Values | Default |
|---|---|---|
| `rating` | `1`–`5` | — (all stars) |
| `ordering` | `-created_at`, `created_at`, `-helpful_count`, `-rating`, `rating` | `-created_at` |
| `page` | integer | 1 |
| `page_size` | integer (max 100) | 10 |

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "count": 87,
        "next": null,
        "previous": null,
        "results": [
            {
                "id": 12,
                "rating": 5,
                "headline": "Excellent course",
                "body": "Well-structured and practical.",
                "helpful_count": 14,
                "not_helpful_count": 1,
                "reviewer_name": "John Doe",
                "viewer_vote": null,
                "created_at": "2026-06-01T10:00:00Z",
                "updated_at": "2026-06-01T10:00:00Z"
            }
        ]
    }
}
```

`viewer_vote` is `"helpful"`, `"not_helpful"`, or `null`. For unauthenticated requests it is always `null`. For authenticated learners it reflects the `ReviewVote` row for that learner.

---

### 44.3 Submit a Review

**POST** `{{base_url}}/courses/{{course_slug}}/reviews/`

**Headers:** learner JWT.

```json
{
    "rating": 5,
    "headline": "Excellent course",
    "body": "Well-structured and practical. Highly recommended."
}
```

**Expected 201 (first submission):**
```json
{
    "success": true,
    "message": "Review submitted.",
    "data": { "id": 12, "rating": 5, "headline": "Excellent course", "...": "..." }
}
```

**Expected 200 (edit — upsert):** Same shape, `"message": "Review updated."`.

| Scenario | Status | Body |
|---|---|---|
| Not enrolled (active) | 403 | `"You must be enrolled in this course to leave a review."` |
| `rating` outside 1–5 | 400 | `errors.rating` |
| Blank `headline` | 400 | `errors.headline: ["Headline must not be blank."]` |
| `headline` over 150 chars | 400 | `errors.headline` |
| Instructor / unenrolled calling POST | 403 | `"Only learners can access this resource."` |

---

### 44.4 Get My Review

**GET** `{{base_url}}/courses/{{course_slug}}/reviews/my-review/`

**Headers:** learner JWT.

**Expected 200:** Same review object shape as 44.2.

**Expected 404** if no review exists yet: `"You have not reviewed this course yet."`

---

### 44.5 Edit My Review

**PATCH** `{{base_url}}/courses/{{course_slug}}/reviews/my-review/`

**Headers:** learner JWT.

Send only the fields to change (all fields are re-validated):

```json
{
    "rating": 4,
    "headline": "Good course, some pacing issues",
    "body": "Content is solid but some sections could move faster."
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Review updated.",
    "data": { "id": 12, "rating": 4, "headline": "Good course, some pacing issues", "...": "..." }
}
```

---

### 44.6 Delete My Review

**DELETE** `{{base_url}}/courses/{{course_slug}}/reviews/my-review/`

**Headers:** learner JWT.

**Body:** *(empty)*

**Expected 200:**
```json
{ "success": true, "message": "Review deleted." }
```

After deletion the course `avg_rating` and `review_count` on the catalog are recalculated automatically.

| Scenario | Status | Body |
|---|---|---|
| No review exists | 404 | `"Review not found."` |

---

### 44.7 Vote on a Review

**POST** `{{base_url}}/courses/reviews/{{review_id}}/vote/`

**Headers:** learner JWT. Save the review `id` from the list response as `{{review_id}}`.

```json
{ "is_helpful": true }
```

**Expected 200:**
```json
{ "success": true, "message": "Marked as helpful." }
```

Or `"Marked as not helpful."` when `is_helpful: false`.

| Scenario | Status | Body |
|---|---|---|
| Review not found / not published | 404 | `"Review not found."` |
| Voting on your own review | 422 | `"You cannot vote on your own review."` |
| Missing `is_helpful` field | 400 | `errors.is_helpful` |
| Voting in same direction again | 200 | Idempotent — same vote returned, no double-count |
| Flipping direction | 200 | Counters updated atomically |

---

### 44.8 Catalog Filter Integration

Reviews integrate with the existing catalog filter/sort:

| Param | Type | Example | Effect |
|---|---|---|---|
| `sort=rating` | — | `?sort=rating` | Orders by `avg_rating DESC`, then `published_at DESC` |
| `rating_min` | decimal 1.0–5.0 | `?rating_min=4.0` | Only courses with `avg_rating ≥ 4.0` |
| `min_reviews` | integer ≥ 0 | `?min_reviews=10` | Only courses with at least 10 reviews |

Combine freely: `?sort=rating&rating_min=4.0&min_reviews=5`

| Bad input | Status | Error |
|---|---|---|
| `?rating_min=0` | 400 | `"rating_min must be between 1 and 5."` |
| `?rating_min=6` | 400 | `"rating_min must be between 1 and 5."` |
| `?min_reviews=-1` | 400 | `"min_reviews must be a non-negative integer."` |

---

# Reference

## 45. Common Error Responses

### 45.1 Invalid `item_type` in section contents
```json
{ "item_type": "video", "title": "Invalid Type" }
```
**Expected 400:** `"item_type must be 'lecture', 'quiz', 'coding', or 'assignment'."`

### 45.2 Invalid reorder position
```json
{ "position": 0 }
```
**Expected 400:** `"position must be a positive integer."`

### 45.3 Two correct answers for one question

Trying to create a second answer with `"is_correct": true` for the same question:

**Expected 400:**
```json
{
    "success": false,
    "message": "Validation failed.",
    "errors": {
        "is_correct": ["A correct answer already exists for this question."]
    }
}
```

---

## 46. Pagination

All list endpoints use page-based pagination.

| Param | Default | Max | Example |
|---|---|---|---|
| `page` | 1 | — | `?page=2` |
| `page_size` | 10 | 100 | `?page_size=25` |

**Example:** `{{base_url}}/courses/catalog/?page=2&page_size=5`

The standard paginated envelope is:
```json
{
    "success": true,
    "data": {
        "count": 42,
        "next": "{{base_url}}/courses/catalog/?page=3",
        "previous": "{{base_url}}/courses/catalog/?page=1",
        "results": []
    }
}
```

---

## 47. Quick Test Flows

### 47.1 Registration / Login

1. **Register** a learner (5.1)
2. **Check OTP** — look in email or DB: `python manage.py shell -c "from authentication.models import User; u=User.objects.get(email='john.learner@example.com'); print(u.otp_code)"`
3. **Verify OTP** (7) with the code
4. **Login** (9) — save the `access` and `refresh` tokens
5. **Logout** (11) with the tokens

### 47.2 Google Sign-In (Full OAuth Flow)

> Prerequisites: set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_CALLBACK_URL` in `.env`. The callback URL must match Google Cloud Console.

**Option A: Backend-only browser flow (recommended — no frontend)**

Make sure `FRONTEND_GOOGLE_CALLBACK` is **not set** in `.env`.

1. Open `{{base_url}}/auth/google/` in your **browser**
2. Sign in with a Google account
3. Backend exchanges the code, creates/finds the user, returns JSON directly
4. Check the browser response: `success: true`, user data, cookies set

**To sign in as instructor:** `{{base_url}}/auth/google/?user_type=instructor`

**Option B: Frontend flow (when `FRONTEND_GOOGLE_CALLBACK` is set)**

1. Open `{{base_url}}/auth/google/` in your browser
2. After consent, backend callback redirects to the frontend with `?code=<code>`
3. Copy the `code` from the frontend URL
4. In Postman, **POST** `{{base_url}}/auth/google/exchange-token/`:
   ```json
   { "code": "<paste_authorization_code_here>", "user_type": "learner" }
   ```

**Option C: Postman-only (for quick testing)**

1. **GET** `{{base_url}}/auth/google/` — disable auto-redirects, copy the `Location` header URL
2. Paste in a browser, complete Google sign-in
3. Grab the `code` param from the redirect URL
4. **POST** `{{base_url}}/auth/google/exchange-token/` with the code

**Things to verify:**
- `access_token` and `refresh_token` cookies are HttpOnly
- New learner has `is_verified: true`, new instructor has `is_verified: false`
- New Google user has no usable password (can't login with password)
- `is_email_verified: true` for all Google sign-ins
- Second sign-in with same Google account reuses existing user (`is_new_user: false`)
- `partner_institution` user_type is rejected with 400
- Existing `partner_institution` account is rejected with 403

### 47.3 Forgot / Reset Password

1. **Forgot Password** (13) for a verified user email
2. **Check OTP** — look in email or DB
3. **Verify OTP** with `purpose: "password_reset"` (14)
4. **Copy `reset_token`** from the OTP-verify response
5. **Reset Password** (15)
6. **Login** with the new password (9)

### 47.4 Profile Management

1. **Register** a learner (5.1)
2. **Verify OTP** (7)
3. **Login** (9) — save the `access_token`
4. **Get My Profile** (16.1)
5. **Update Profile** (16.2) — add headline, bio, location
6. **Create Education** (17.2)
7. **Create Work Experience** (18.2)
8. **Get My Profile** again — verify education & work experience are included
9. **View Public Profile** (19) using the slug from the profile response

### 47.5 Public Browsing

1. Register and verify a few users of different types
2. Update their profiles with location data
3. **Browse Learners** (20.1) — try with and without filters
4. **Browse Instructors** (20.2)
5. **Browse Institutions** (20.3)
6. **View Individual Profile** (19) by slug

### 47.6 Instructor ID Verification (Full Cycle)

1. **Register** an instructor (5.2)
2. **Verify OTP** (7)
3. **Login** as the instructor (9) — save the `access_token`
4. **Complete profile** (16.2b) — fill `headline`, `bio`, `specialization`, `years_of_experience`, `current_title`
5. **Create draft** (21) — note the verification `id`
6. **Update** with document details (22.1)
7. **Upload documents** (22.2) — form-data with front image & selfie
8. *(Optional)* Upload resume (22.2)
9. **Submit** (23) — transitions to `submitted` (fails if profile is incomplete)
10. **Login as admin** — save the admin `access_token`
11. **List verifications** (25.1) with `?status=submitted`
12. **View detail** (25.2)
13. **Pick up** (25.3a) — transitions to `under_review`
14. **Approve** (25.3b) — instructor is now verified
15. **Login as instructor** again → **Get My Profile** (16.1) — confirm `is_verified: true`

### 47.7 Rejection & Resubmission

1. Complete 45.6 steps 1–13 (up to `under_review`)
2. **Reject** (25.3c) with a reason
3. **Login as instructor** → **List verifications** (24.1) — see `rejected` status with reason
4. Instructor creates a **new draft** (21) and goes through the flow again

### 47.8 Action Required & Resubmit

1. Complete 45.6 steps 1–13 (up to `under_review`)
2. **Request action** (25.3d) — admin specifies what needs to be fixed
3. **Login as instructor** → **List verifications** (24.1) — see `action_required` status and reason
4. **Update** the verification (22) — fix the issue (e.g., re-upload clearer document)
5. **Submit** again (23) — transitions back to `submitted` (profile completeness is checked again)
6. **Admin picks up** and **approves** (25.3a, 25.3b)

### 47.9 Course Authoring (Full E2E)

1. Login as a verified instructor and copy the access token.
2. **Create course** (26.1) → save `course_id`.
3. Set **learning_objectives / prerequisites / audiences** (newline-separated text) in the create or a follow-up PATCH (27).
4. **Create section** (28.1) → save `section_id`.
5. Create one **article lecture** via `sections/{id}/contents/` (29.1).
6. Create one **quiz** via `sections/{id}/contents/` (29.3) → save `quiz_id`.
7. Create one **assignment** (29.5) → save `assignment_id`.
8. Create one **coding exercise** (29.4) → save `exercise_id`.
9. **List `sections/{id}/contents/`** — verify all four items appear with correct `content` summaries.
10. **Reorder** coding exercise to position 1 (29.7); verify list updates.
11. Add **one question and two answers** (one correct) to the quiz (31.2, 31.5).
12. Add **three questions** to the assignment (32.5); reorder them (32.7); `GET assignments/{id}/` and verify `max_score` equals the sum of question points.
13. Add a **Python language config** to the exercise (33.2).
14. Add two **test cases** (one visible, one hidden) to the exercise (33.5).
15. **GET coding-exercises/{id}/** — verify `language_configs` and `test_cases` arrays are populated.
16. Patch exercise difficulty to `"hard"` (33.1).
17. **Delete** the exercise (33.1) — re-fetch `sections/{id}/contents/` and confirm the slot is gone.
18. **Delete** the assignment (32.4) — re-fetch and confirm the slot is gone (its questions cascade away).
19. Call **submit** (34.1) — expect 400 because the section is missing content. Re-add content, then re-submit — expect 200 with `status: under_review`.
20. As an **admin**, call **review** (34.2) with `{"action": "approve"}` — expect `status: published`.
21. Call **archive** (34.5) — expect `status: archived`.

### 47.10 Certificates & Reviews (Full E2E)

Prerequisites: a learner enrolled in a published course who has completed all content items (progress = 100%).

| Step | Action | What to verify |
|---|---|---|
| 1 | Complete all lectures / quizzes / assignments / exercises | `progress_percent` reaches 100 on `/my-courses/` |
| 2 | `GET /courses/my-courses/{{course_slug}}/certificate/` (learner JWT) | 200, `certificate_uid` in response; save as `{{certificate_uid}}` |
| 3 | `GET /courses/certificates/{{certificate_uid}}/verify/` (no auth) | 200, `is_valid: true` |
| 4 | `GET /courses/certificates/{{certificate_uid}}/download/` (no auth) | 200, PDF bytes; open in Postman visualizer or save file |
| 5 | `GET /courses/{{course_slug}}/reviews/summary/` (no auth) | 200, `review_count: 0` initially |
| 6 | `POST /courses/{{course_slug}}/reviews/` (learner JWT) `{"rating":5,"headline":"Great course","body":""}` | 201, save `id` as `{{review_id}}` |
| 7 | `GET /courses/{{course_slug}}/reviews/summary/` (no auth) | 200, `review_count: 1`, `avg_rating: 5.0` |
| 8 | `GET /courses/{{course_slug}}/reviews/` (learner2 JWT) | 200, `viewer_vote: null` |
| 9 | `POST /courses/reviews/{{review_id}}/vote/` (learner2 JWT) `{"is_helpful":true}` | 200, `"Marked as helpful."` |
| 10 | `GET /courses/{{course_slug}}/reviews/` (learner2 JWT) | 200, `viewer_vote: "helpful"` on that review |
| 11 | `POST /courses/reviews/{{review_id}}/vote/` (learner2 JWT) `{"is_helpful":false}` | 200, `"Marked as not helpful."` — flip updates counters atomically |
| 12 | `PATCH /courses/{{course_slug}}/reviews/my-review/` (learner JWT) `{"rating":4,"headline":"Good course"}` | 200, `avg_rating` on catalog updates |
| 13 | `DELETE /courses/{{course_slug}}/reviews/my-review/` (learner JWT) | 200, `review_count` drops back to 0 |

### 47.12 Catalog → Enrollment → Consumption

| Step | Method | URL | Auth | What to verify |
|---|---|---|---|---|
| 1 | GET | `{{base_url}}/courses/catalog/` | None | 200, list of published courses |
| 2 | GET | `{{base_url}}/courses/catalog/{{course_slug}}/` | None | 200, full course detail |
| 3 | GET | `{{base_url}}/courses/catalog/?search=python` | None | 200, filtered results matching title / description / instructor name |
| 4 | GET | `{{base_url}}/courses/catalog/?level=beginner,intermediate` | None | 200, only beginner OR intermediate courses |
| 5 | GET | `{{base_url}}/courses/catalog/?price_type=free&sort=newest` | None | 200, only free courses, newest first |
| 6 | GET | `{{base_url}}/courses/catalog/?price_min=10&price_max=99&duration_min=60&sort=price_asc` | None | 200, range filters AND'd, ordered by price asc |
| 7 | GET | `{{base_url}}/courses/catalog/?sort=cheapest` | None | 400, `errors.sort` lists the valid sort keys |
| 8 | GET | `{{base_url}}/courses/catalog/?level=wizard&price_min=-1` | None | 400, both `errors.level` and `errors.price_min` populated in one response |
| 9 | POST | `{{base_url}}/courses/{{course_slug}}/enroll/` | Learner | 201, `is_active: true`, `enrollment_type: "free"` |
| 10 | POST | `{{base_url}}/courses/{{course_slug}}/enroll/` | Learner | 422, duplicate error |
| 11 | GET | `{{base_url}}/courses/my-courses/` | Learner | 200, enrolled course appears |
| 12 | GET | `{{base_url}}/courses/my-courses/{{course_slug}}/` | Learner | 200, `last_accessed_at` is now set |
| 13 | GET | `{{base_url}}/courses/learn/{{course_slug}}/curriculum/` | Learner | 200, lightweight outline; `is_completed` per item |
| 14 | GET | `{{base_url}}/courses/learn/lectures/{{lecture_id}}/` | Learner | 200, HLS or article payload + `progress` block |
| 15 | POST | `{{base_url}}/courses/learn/lectures/{{lecture_id}}/progress/` | Learner | 200, idempotent upsert; flips `is_completed` when cursor hits duration |
| 16 | GET | `{{base_url}}/courses/learn/quizzes/{{quiz_id}}/` | Learner | 200, questions + options (no `is_correct`) |
| 17 | POST | `{{base_url}}/courses/learn/quizzes/{{quiz_id}}/submit/` | Learner | 200, per-question verdict + score |
| 18 | POST | `{{base_url}}/courses/learn/assignments/{{assignment_id}}/submit/` | Learner | 202, returns `status='submitted'` and a `submission_id` to poll |
| 19 | GET | `{{base_url}}/courses/learn/assignments/submissions/{{submission_id}}/` | Learner | poll until `status in ('passed', 'failed', 'grading_failed')` |
| 20 | GET | `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/` | Learner | 200, starter code + visible test cases only (no `solution_code`) |
| 21 | POST | `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/run/` | Learner | 202, returns `{task_id}` |
| 22 | GET | `{{base_url}}/courses/learn/coding-exercises/tasks/{{task_id}}/` | Learner | poll until `state='SUCCESS'`, visible-tests-only result dict |
| 23 | POST | `{{base_url}}/courses/learn/coding-exercises/{{exercise_id}}/submit/` | Learner | 202, returns queued `CodingSubmission` row |
| 24 | GET | `{{base_url}}/courses/learn/coding-exercises/submissions/{{submission_id}}/` | Learner | poll until `status in ('passed', 'failed', 'error')`; hidden tests redacted |
| 25 | POST | `{{base_url}}/courses/{{course_slug}}/unenroll/` | Learner | 200, `is_active: false` |
| 26 | GET | `{{base_url}}/courses/my-courses/` | Learner | 200, list is now empty |
| 27 | POST | `{{base_url}}/courses/{{course_slug}}/enroll/` | Learner | 201, same enrollment `id`, reactivated |

---

## 48. Notes

- All ownership checks are instructor-scoped on the authoring side; if the course/section/exercise/assignment is not yours, the API returns 404 (not 403). The "leak existence" hardening is also applied to numeric-ID learner endpoints.
- Status transitions (submit, review, rework, archive) are dedicated POST endpoints. Do not set `status` directly via PATCH — the field is not writable that way.
- `solution_code` on language configs is stored server-side and must never appear in learner-facing responses.
- Hidden test cases (`is_hidden: true`) are for grading only and must never be exposed to learners.
- `model_answer` on assignment questions is instructor-only and is stripped from learner-facing responses (and on submission detail, revealed only after grading reaches a terminal state).
- For video lectures, transcoding states usually move `processing → ready` (or `failed` on error).
- Assignments, lectures, quizzes, and coding exercises share one ordering layer (`SectionContent`); reorder via `PATCH courses/contents/{content_id}/reorder/` regardless of item type.
- `update_last_accessed` on `/my-courses/{slug}/` is **debounced** to 5 minutes — repeated GETs within that window do not re-write the row.
- `progress_percent` is recomputed by signal whenever a lecture's `is_completed` flips, a quiz attempt is created, or an assignment submission transitions to `passed`. Re-fetch `/my-courses/` to see the updated rollup.
- For learner endpoints that take a numeric ID (lecture, quiz, assignment, submission), unauthorized access returns 404 to avoid leaking the existence of resources. Slug-based endpoints (`/my-courses/<slug>/`, `/learn/<slug>/curriculum/`) return 403 since the slug is already public via the catalog.
</content>
</invoke>