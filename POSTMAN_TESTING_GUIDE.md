# Postman Testing Guide — Registration, Login, Forgot Password, Reset Password, Profiles & ID Verification

## Table of Contents

### Authentication
- [1. Register — Learner](#1-register--learner)
- [2. Register — Instructor](#2-register--instructor)
- [3. Register — Partner Institution](#3-register--partner-institution)
- [4. Register — Validation Error Cases](#4-register--validation-error-cases)
- [5. Verify OTP](#5-verify-otp-required-before-login)
- [6. Resend OTP](#6-resend-otp)
- [7. Login](#7-login)
- [8. Logout](#8-logout)

### Password Management
- [9. Forgot Password](#9-forgot-password-send-password-reset-otp)
- [10. Verify OTP for Password Reset](#10-verify-otp-for-password-reset)
- [11. Reset Password](#11-reset-password)

### Profile Management
- [12. My Profile — Get](#12-my-profile--get)
- [13. My Profile — Update (PATCH)](#13-my-profile--update-patch)
- [14. Education — List & Create](#14-education--list--create)
- [15. Education — Update & Delete](#15-education--update--delete)
- [16. Work Experience — List & Create](#16-work-experience--list--create)
- [17. Work Experience — Update & Delete](#17-work-experience--update--delete)

### Public Profiles
- [18. Public Profile — View by Slug](#18-public-profile--view-by-slug)
- [19. Public Profile Lists — Browse Learners](#19-public-profile-lists--browse-learners)
- [20. Public Profile Lists — Browse Instructors](#20-public-profile-lists--browse-instructors)
- [21. Public Profile Lists — Browse Institutions](#21-public-profile-lists--browse-institutions)

### Instructor ID Verification
- [22. Create Draft Verification](#22-create-draft-verification)
- [23. Update Draft Verification](#23-update-draft-verification)
- [24. Submit Verification](#24-submit-verification)
- [25. List My Verifications](#25-list-my-verifications)
- [26. View Single Verification](#26-view-single-verification)

### Admin — Verification Management
- [27. Admin — List All Verifications](#27-admin--list-all-verifications)
- [28. Admin — View Verification Detail](#28-admin--view-verification-detail)
- [29. Admin — Review Verification](#29-admin--review-verification)

### Quick Test Flows
- [Registration / Login](#quick-test-flow--registrationlogin)
- [Forgot / Reset Password](#quick-test-flow--forgotreset-password)
- [Profile Management](#quick-test-flow--profile-management)
- [Public Browsing](#quick-test-flow--public-browsing)
- [Instructor ID Verification (Full Cycle)](#quick-test-flow--instructor-id-verification-full-cycle)
- [Rejection & Resubmission](#quick-test-flow--rejection--resubmission)
- [Action Required & Resubmit](#quick-test-flow--action-required--resubmit)

---

**Base URL:** `http://127.0.0.1:8000/api/v1/auth`

> Start the server: `python manage.py runserver`

---

## 1. Register — Learner

**POST** `/register/`

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

---

## 2. Register — Instructor

**POST** `/register/`

```json
{
    "email": "sarah.instructor@example.com",
    "full_name": "Sarah Williams",
    "password": "Teach@5678",
    "confirm_password": "Teach@5678",
    "user_type": "instructor"
}
```

**Expected 201:**
```json
{
    "success": true,
    "message": "Registration successful. OTP sent to your email.",
    "data": {
        "user_id": 2,
        "email": "sarah.instructor@example.com",
        "full_name": "Sarah Williams",
        "user_type": "instructor",
        "is_email_verified": false,
        "is_verified": false
    }
}
```

---

## 3. Register — Partner Institution

**POST** `/register/`

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

**Expected 201:**
```json
{
    "success": true,
    "message": "Registration successful. OTP sent to your email.",
    "data": {
        "user_id": 3,
        "email": "admin@techuniversity.edu",
        "full_name": "Tech University",
        "user_type": "partner_institution",
        "is_email_verified": false,
        "is_verified": false,
        "institution_name": "Tech University",
        "institution_type": "university"
    }
}
```

### institution_type options:
| Value             | Label               |
|-------------------|---------------------|
| `university`      | University          |
| `college`         | College             |
| `training_center` | Training Center     |
| `corporate`       | Corporate Training  |
| `nonprofit`       | Non-Profit          |
| `other`           | Other               |

---

## 4. Register — Validation Error Cases

### 4a. Missing user_type

```json
{
    "email": "test@example.com",
    "full_name": "Test User",
    "password": "Secure@1234",
    "confirm_password": "Secure@1234"
}
```

**Expected 400:** `user_type` — This field is required.

### 4b. Partner institution with generic email

```json
{
    "email": "partner@gmail.com",
    "full_name": "Some Institute",
    "password": "Partner@9012",
    "confirm_password": "Partner@9012",
    "user_type": "partner_institution",
    "institution_name": "Some Institute",
    "institution_type": "college"
}
```

**Expected 400:** `email` — Partner institutions must register with an official institutional email address, not a personal email.

### 4c. Partner institution missing institution_name

```json
{
    "email": "admin@somecollege.edu",
    "full_name": "Some College",
    "password": "Partner@9012",
    "confirm_password": "Partner@9012",
    "user_type": "partner_institution",
    "institution_type": "college"
}
```

**Expected 400:** `institution_name` — Institution name is required for partner institution registration.

### 4d. Partner institution missing institution_type

```json
{
    "email": "admin@somecollege.edu",
    "full_name": "Some College",
    "password": "Partner@9012",
    "confirm_password": "Partner@9012",
    "user_type": "partner_institution",
    "institution_name": "Some College"
}
```

**Expected 400:** `institution_type` — Institution type is required for partner institution registration.

### 4e. Password mismatch

```json
{
    "email": "test2@example.com",
    "full_name": "Test User",
    "password": "Secure@1234",
    "confirm_password": "Different@5678",
    "user_type": "learner"
}
```

**Expected 400:** `confirm_password` — Passwords do not match.

### 4f. Duplicate email

```json
{
    "email": "john.learner@example.com",
    "full_name": "Another John",
    "password": "Secure@1234",
    "confirm_password": "Secure@1234",
    "user_type": "learner"
}
```

**Expected 400:** `email` — A user with this email already exists.

---

## 5. Verify OTP (required before login)

**POST** `/otp/verify/`

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
{
    "success": true,
    "message": "Email verified successfully."
}
```

---

## 6. Resend OTP

**POST** `/otp/resend/`

```json
{
    "email": "john.learner@example.com",
    "purpose": "registration"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "OTP resent successfully."
}
```

---

## 7. Login

**POST** `/login/`

> User must have verified email before login works.

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

### Login error cases

**Unverified email:**
```json
{
    "email": "sarah.instructor@example.com",
    "password": "Teach@5678"
}
```
**Expected 401:** Invalid credentials or account issue.

**Wrong password:**
```json
{
    "email": "john.learner@example.com",
    "password": "WrongPassword@1"
}
```
**Expected 401:** Invalid credentials or account issue.

---

## 8. Logout

**POST** `/logout/`

**Headers:**
```
Authorization: Bearer <access_token>
```

**Body:**
```json
{
    "refresh": "<refresh_token>"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Logged out successfully."
}
```

---

## 9. Forgot Password (send password reset OTP)

**POST** `/password/forgot/`

```json
{
    "email": "john.learner@example.com"
}
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

**Email not found:**
```json
{
    "email": "unknown.user@example.com"
}
```
**Expected 400:** `email` - No account found with this email.

**Email not verified:**
```json
{
    "email": "sarah.instructor@example.com"
}
```
**Expected 400:** `email` - Email must be verified before password reset.

---

## 10. Verify OTP for Password Reset

**POST** `/otp/verify/`

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

> Save `reset_token` from this response. It is required for the next step.

---

## 11. Reset Password

**POST** `/password/reset/`

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

**Token expired or invalid:**
```json
{
    "email": "john.learner@example.com",
    "reset_token": "invalid_or_expired_token",
    "new_password": "NewSecure@1234",
    "confirm_password": "NewSecure@1234"
}
```
**Expected 400:** `reset_token` - Invalid or expired reset token.

**Password mismatch:**
```json
{
    "email": "john.learner@example.com",
    "reset_token": "<password_reset_token>",
    "new_password": "NewSecure@1234",
    "confirm_password": "Different@5678"
}
```
**Expected 400:** `confirm_password` - Passwords do not match.

---

## Quick Test Flow — Registration/Login

1. **Register** a learner (step 1)
2. **Check OTP** — look in email or DB: `python manage.py shell -c "from auth.models import User; u=User.objects.get(email='john.learner@example.com'); print(u.otp_code)"`
3. **Verify OTP** (step 5) with the code
4. **Login** (step 7) — save the `access` and `refresh` tokens
5. **Logout** (step 8) with the tokens

## Quick Test Flow — Forgot/Reset Password

1. **Forgot Password** (step 9) for a verified user email
2. **Check OTP** — look in email or DB
3. **Verify OTP** using `purpose: "password_reset"` (step 10)
4. **Copy reset_token** from the OTP verify response
5. **Reset Password** (step 11)
6. **Login** with the new password (step 7)

---

## 12. My Profile — Get

**GET** `/profile/me/`

**Headers:**
```
Authorization: Bearer <access_token>
```

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

### My Profile error cases

**No auth token:**
**Expected 401:** Authentication credentials were not provided.

**Email not verified:**
**Expected 403:** Your email must be verified before accessing this resource.

---

## 13. My Profile — Update (PATCH)

**PATCH** `/profile/me/`

**Headers:**
```
Authorization: Bearer <access_token>
```

### 13a. Update Learner Profile

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

**Expected 200:**
```json
{
    "success": true,
    "message": "Profile updated successfully.",
    "data": { "...updated fields..." }
}
```

### 13b. Update Instructor Profile

> Login as instructor first.

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

### 13c. Update Partner Institution Profile

> Login as partner institution first.

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

### experience_level options (Learner):
| Value      | Label                       |
|------------|-----------------------------|
| `student`  | Student / No experience     |
| `entry`    | Entry level (0–2 years)     |
| `mid`      | Mid level (3–5 years)       |
| `senior`   | Senior level (6–10 years)   |
| `expert`   | Expert (10+ years)          |

---

## 13d. Upload Profile Photo / Logo (form-data)

> Profile photo uploads must use **form-data** instead of JSON because files cannot be sent as JSON.

**PATCH** `/profile/me/`

**Headers:**
```
Authorization: Bearer <access_token>
```

> In Postman: go to **Body** → select **form-data** (not raw JSON).

### Learner / Instructor — Upload Profile Photo

| Key             | Type | Value                          |
|-----------------|------|--------------------------------|
| `profile_photo` | File | *(select an image file)*       |
| `headline`      | Text | Data Analyst at Google         |

> You can combine file fields with text fields in the same form-data request.

**Expected 200:**
```json
{
    "success": true,
    "message": "Profile updated successfully.",
    "data": {
        "profile_photo": "/learner_profiles/photos/my_photo.jpg",
        "headline": "Data Analyst at Google",
        "...other fields..."
    }
}
```

### Partner Institution — Upload Logo & Cover Image

| Key            | Type | Value                          |
|----------------|------|--------------------------------|
| `logo`         | File | *(select a logo image file)*   |
| `cover_image`  | File | *(select a cover image file)*  |
| `tagline`      | Text | Leading the future of tech     |

**Expected 200:**
```json
{
    "success": true,
    "message": "Profile updated successfully.",
    "data": {
        "logo": "/partner_institutions/logos/university_logo.png",
        "cover_image": "/partner_institutions/covers/university_cover.jpg",
        "tagline": "Leading the future of tech",
        "...other fields..."
    }
}
```

### Remove Profile Photo (set to null)

| Key             | Type | Value |
|-----------------|------|-------|
| `profile_photo` | File | *(leave empty)* |

> Send the field with an empty value to clear the photo.

### Postman form-data tips for file uploads

1. In Postman, click **Body** → **form-data**
2. For file fields: hover over the **Key** field, click the dropdown on the right, and change it from **Text** to **File**
3. Click **Select Files** in the **Value** column to pick an image
4. You can mix file and text fields in the same request
5. **Do NOT** set `Content-Type` header manually — Postman sets `multipart/form-data` with the boundary automatically

---

## 14. Education — List & Create

### 14a. List My Education

**GET** `/profile/me/education/`

**Headers:**
```
Authorization: Bearer <access_token>
```

**Expected 200:**
```json
{
    "success": true,
    "data": []
}
```

### 14b. Create Education Entry

**POST** `/profile/me/education/`

**Headers:**
```
Authorization: Bearer <access_token>
```

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

### 14c. Create Current Education (no end_date)

```json
{
    "degree": "master",
    "field_of_study": "Data Science",
    "institution": "Stanford University",
    "start_date": "2024-09-01",
    "is_current": true
}
```

### degree options:
| Value          | Label              |
|----------------|--------------------|
| `high_school`  | High School        |
| `associate`    | Associate Degree   |
| `bachelor`     | Bachelor's Degree  |
| `master`       | Master's Degree    |
| `doctorate`    | Doctorate          |
| `diploma`      | Diploma            |
| `certificate`  | Certificate        |
| `other`        | Other              |

### Education validation errors

**Current education with end_date:**
```json
{
    "degree": "master",
    "institution": "Stanford",
    "start_date": "2024-09-01",
    "end_date": "2025-06-01",
    "is_current": true
}
```
**Expected 400:** `end_date` — Current education should not have an end date.

**Completed education without end_date:**
```json
{
    "degree": "bachelor",
    "institution": "MIT",
    "start_date": "2018-09-01",
    "is_current": false
}
```
**Expected 400:** `end_date` — End date is required for completed education.

**Partner institution user trying to add education:**
**Expected 403:** Education entries are only available for learners and instructors.

---

## 15. Education — Update & Delete

### 15a. Update Education Entry (PATCH)

**PATCH** `/profile/me/education/1/`

**Headers:**
```
Authorization: Bearer <access_token>
```

```json
{
    "field_of_study": "Computer Science & Engineering"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Education entry updated.",
    "data": { "...updated fields..." }
}
```

### 15b. Delete Education Entry

**DELETE** `/profile/me/education/1/`

**Headers:**
```
Authorization: Bearer <access_token>
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Education entry deleted."
}
```

---

## 16. Work Experience — List & Create

### 16a. List My Work Experience

**GET** `/profile/me/work-experience/`

**Headers:**
```
Authorization: Bearer <access_token>
```

**Expected 200:**
```json
{
    "success": true,
    "data": []
}
```

### 16b. Create Work Experience Entry

**POST** `/profile/me/work-experience/`

**Headers:**
```
Authorization: Bearer <access_token>
```

```json
{
    "job_title": "Data Analyst",
    "company": "Google",
    "location": "San Francisco, CA",
    "start_date": "2022-07-01",
    "is_current": true
}
```

**Expected 201:**
```json
{
    "success": true,
    "message": "Work experience entry created.",
    "data": {
        "id": 1,
        "job_title": "Data Analyst",
        "company": "Google",
        "location": "San Francisco, CA",
        "start_date": "2022-07-01",
        "end_date": null,
        "is_current": true,
        "created_at": "...",
        "updated_at": "..."
    }
}
```

### 16c. Create Past Position

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

### Work experience validation errors

**Current position with end_date:**
**Expected 400:** `end_date` — Current position should not have an end date.

**Past position without end_date:**
**Expected 400:** `end_date` — End date is required for past positions.

---

## 17. Work Experience — Update & Delete

### 17a. Update Work Experience (PATCH)

**PATCH** `/profile/me/work-experience/1/`

**Headers:**
```
Authorization: Bearer <access_token>
```

```json
{
    "job_title": "Senior Data Analyst"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Work experience entry updated.",
    "data": { "...updated fields..." }
}
```

### 17b. Delete Work Experience

**DELETE** `/profile/me/work-experience/1/`

**Headers:**
```
Authorization: Bearer <access_token>
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Work experience entry deleted."
}
```

---

## 18. Public Profile — View by Slug

**GET** `/profiles/<slug>/`

> No authentication required. Replace `<slug>` with the user's `name_slug`.

**Example:** `GET /profiles/john-doe/`

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
            {
                "degree": "bachelor",
                "field_of_study": "Computer Science",
                "institution": "MIT",
                "start_date": "2018-09-01",
                "end_date": "2022-06-15",
                "is_current": false
            }
        ],
        "work_experience": [
            {
                "job_title": "Data Analyst",
                "company": "Google",
                "location": "San Francisco, CA",
                "start_date": "2022-07-01",
                "end_date": null,
                "is_current": true
            }
        ]
    }
}
```

### Public profile error cases

**Non-existent slug:**
`GET /profiles/unknown-user/`
**Expected 404:** Profile not found.

**Learner with `is_profile_public: false`:**
**Expected 404:** Profile not found.

---

## 19. Public Profile Lists — Browse Learners

**GET** `/profiles/learners/`

> No authentication required. Supports pagination and filters.

**Query parameters:**
| Param              | Example     | Description                          |
|--------------------|-------------|--------------------------------------|
| `page`             | `1`         | Page number                          |
| `page_size`        | `10`        | Results per page (max 100)           |
| `country`          | `USA`       | Filter by country (case-insensitive) |
| `experience_level` | `mid`       | Filter by experience level           |

**Example:** `GET /profiles/learners/?country=USA&experience_level=mid`

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

---

## 20. Public Profile Lists — Browse Instructors

**GET** `/profiles/instructors/`

> No authentication required. Supports pagination and filters.

**Query parameters:**
| Param         | Example | Description                          |
|---------------|---------|--------------------------------------|
| `page`        | `1`     | Page number                          |
| `page_size`   | `10`    | Results per page (max 100)           |
| `country`     | `USA`   | Filter by country (case-insensitive) |
| `is_verified` | `true`  | Filter by verification status        |

**Example:** `GET /profiles/instructors/?is_verified=true`

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

---

## 21. Public Profile Lists — Browse Institutions

**GET** `/profiles/institutions/`

> No authentication required. Supports pagination and filters.

**Query parameters:**
| Param              | Example       | Description                          |
|--------------------|---------------|--------------------------------------|
| `page`             | `1`           | Page number                          |
| `page_size`        | `10`          | Results per page (max 100)           |
| `country`          | `USA`         | Filter by country (case-insensitive) |
| `institution_type` | `university`  | Filter by institution type           |

**Example:** `GET /profiles/institutions/?institution_type=university`

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

## Quick Test Flow — Profile Management

1. **Register** a learner (step 1)
2. **Verify OTP** (step 5)
3. **Login** (step 7) — save the `access` token
4. **Get My Profile** (step 12)
5. **Update Profile** (step 13a) — add headline, bio, location
6. **Create Education** (step 14b)
7. **Create Work Experience** (step 16b)
8. **Get My Profile** again (step 12) — verify education & work experience are included
9. **View Public Profile** (step 18) using the slug from the profile response

## Quick Test Flow — Public Browsing

1. Register and verify a few users of different types
2. Update their profiles with location data
3. **Browse Learners** (step 19) — try with and without filters
4. **Browse Instructors** (step 20)
5. **Browse Institutions** (step 21)
6. **View Individual Profile** (step 18) by slug

---

## Postman Tips

- Set **Content-Type** to `application/json` on all requests (except file uploads — use form-data)
- Create environment variables:
  - `base_url` = `http://127.0.0.1:8000/api/v1/auth`
  - `verification_url` = `http://127.0.0.1:8000/api/v1/verification`
- After login, save `access` token to environment and use `Bearer {{access_token}}` in the Authorization tab for authenticated endpoints

---

# Instructor Identity Verification

**Base URL:** `http://127.0.0.1:8000/api/v1/verification`

> Only instructors with verified email can use these endpoints. Login as an instructor first.

### Prerequisites

1. Register an instructor (step 2)
2. Verify OTP (step 5)
3. Login as the instructor (step 7) — save the `access` token

### Verification Lifecycle

```
draft → submitted → under_review → approved
                                  → rejected
                                  → action_required → submitted (resubmit)
```

- **draft**: Instructor is filling in details (can edit freely)
- **submitted**: Waiting for admin to pick up
- **under_review**: Admin is reviewing
- **approved**: Instructor is now verified
- **rejected**: Denied with a reason
- **action_required**: Admin asked instructor to fix something (instructor can edit and resubmit)

### document_type options:
| Value              | Label              |
|--------------------|--------------------|
| `national_id`      | National ID Card   |
| `passport`         | Passport           |
| `drivers_license`  | Driver's License   |
| `residence_permit` | Residence Permit   |

---

## 22. Create Draft Verification

**POST** `/create/`

**Headers:**
```
Authorization: Bearer <instructor_access_token>
```

> All fields are optional at this stage. You can create an empty draft and fill in details later.

### 22a. Create empty draft

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

### 22b. Create draft with partial data

```json
{
    "document_type": "passport",
    "issuing_country": "USA"
}
```

**Expected 201:** Same structure with those fields filled in.

### Create verification error cases

**Non-instructor user:**
**Expected 403:** Only instructors can access this resource.

**Already has an active request:**

> If you already have a verification in `draft`, `submitted`, `under_review`, or `action_required` status:

**Expected 400:**
```json
{
    "success": false,
    "message": "Validation failed.",
    "errors": {
        "non_field_errors": ["You already have a verification request in progress."]
    }
}
```

**Email not verified:**
**Expected 403:** Your email must be verified before accessing this resource.

---

## 23. Update Draft Verification

**PATCH** `/1/update/`

> Replace `1` with your verification ID. Only works when status is `draft` or `action_required`.

**Headers:**
```
Authorization: Bearer <instructor_access_token>
```

### 23a. Update text fields (JSON)

```json
{
    "document_type": "national_id",
    "document_number": "AB1234567",
    "issuing_country": "Bangladesh",
    "expiry_date": "2030-12-31"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Verification updated.",
    "data": {
        "id": 1,
        "document_type": "national_id",
        "document_number": "AB1234567",
        "issuing_country": "Bangladesh",
        "expiry_date": "2030-12-31",
        "...other fields..."
    }
}
```

### 23a-resume. Update with resume (optional)

```json
{
    "document_type": "national_id",
    "document_number": "AB1234567",
    "issuing_country": "Bangladesh"
}
```

> Resume can be uploaded separately using form-data (section 23b).

### 23b. Upload documents (form-data)

> File uploads must use **form-data** (not raw JSON).

**PATCH** `/1/update/`

| Key              | Type | Value                                  |
|------------------|------|----------------------------------------|
| `document_front` | File | *(select front image of your ID)*      |
| `document_back`  | File | *(select back image, if applicable)*   |
| `selfie`         | File | *(select selfie holding your ID)*      |
| `resume`         | File | *(select resume/CV, optional)*         |

**Expected 200:** Same structure with file URLs populated.

### 23c. Full update with PUT (all fields required)

**PUT** `/1/update/`

> Use **form-data** to include both text and file fields.

| Key              | Type | Value                          |
|------------------|------|--------------------------------|
| `document_type`  | Text | `passport`                     |
| `document_number`| Text | `AB1234567`                    |
| `issuing_country`| Text | `Bangladesh`                   |
| `expiry_date`    | Text | `2030-12-31`                   |
| `document_front` | File | *(select front image)*         |
| `selfie`         | File | *(select selfie image)*        |
| `resume`         | File | *(select resume/CV, optional)* |

### Update verification error cases

**Verification not in editable status (e.g., already submitted):**
**Expected 404:** Not found.

**Another user's verification:**
**Expected 404:** Not found.

**Expired document:**
```json
{
    "expiry_date": "2020-01-01"
}
```
**Expected 400:** `expiry_date` — Document has already expired.

---

## 24. Submit Verification

**POST** `/1/submit/`

> Replace `1` with your verification ID. Transitions from `draft` → `submitted` or `action_required` → `submitted`.

**Headers:**
```
Authorization: Bearer <instructor_access_token>
```

**Body:** *(empty — no body needed)*

### Prerequisites

Before submitting, **two sets of requirements** must be satisfied:

**1. Instructor profile must be complete** — these profile fields must be filled:

| Field                 | Description                              |
|-----------------------|------------------------------------------|
| `headline`            | Professional tagline                     |
| `bio`                 | Professional biography (non-empty)       |
| `specialization`      | At least one area of expertise           |
| `years_of_experience` | Must be greater than 0                   |
| `current_title`       | Current job title                        |

> Update your profile first via **PATCH** `/api/v1/auth/profile/me/` (step 13b).

**2. Verification document fields must be filled:**

| Field            | Required |
|------------------|----------|
| `document_type`  | Yes      |
| `document_number`| Yes      |
| `issuing_country`| Yes      |
| `document_front` | Yes      |
| `selfie`         | Yes      |
| `document_back`  | No       |
| `expiry_date`    | No       |
| `resume`         | No       |

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

**Missing required document fields:**

**Expected 400:**
```json
{
    "success": false,
    "message": "['document_front: This field is required before submitting.', 'selfie: This field is required before submitting.']"
}
```

**Verification already submitted:**
**Expected 404:** Not found. *(Only `draft` and `action_required` can be submitted)*

---

## 25. List My Verifications

**GET** `/my/`

**Headers:**
```
Authorization: Bearer <instructor_access_token>
```

**Expected 200:**
```json
{
    "success": true,
    "data": [
        {
            "id": 1,
            "document_type": "national_id",
            "document_number": "AB1234567",
            "issuing_country": "Bangladesh",
            "expiry_date": "2030-12-31",
            "document_front": "/id_verification/documents/front/...",
            "document_back": null,
            "selfie": "/id_verification/selfies/...",
            "status": "submitted",
            "rejection_reason": "",
            "action_required_reason": "",
            "reviewed_by_email": null,
            "reviewed_at": null,
            "created_at": "...",
            "submitted_at": "...",
            "updated_at": "..."
        }
    ]
}
```

> Returns all verifications for the logged-in instructor (most recent first).

---

## 26. View Single Verification

**GET** `/my/1/`

> Replace `1` with the verification ID.

**Headers:**
```
Authorization: Bearer <instructor_access_token>
```

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "id": 1,
        "document_type": "national_id",
        "status": "submitted",
        "...all fields..."
    }
}
```

### Error cases

**Verification belongs to another user:**
**Expected 404:** Not found.

---

# Admin — Verification Management

> These endpoints require an admin (staff) user. Login as a superuser or staff account.

**Create a superuser if needed:**
```
python manage.py createsuperuser
```

---

## 27. Admin — List All Verifications

**GET** `/admin/list/`

**Headers:**
```
Authorization: Bearer <admin_access_token>
```

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

### Filter by status

`GET /admin/list/?status=submitted` — Show only submitted requests.

| Status Value       | Description                  |
|--------------------|------------------------------|
| `draft`            | Not yet submitted            |
| `submitted`        | Waiting for admin review     |
| `under_review`     | Admin is reviewing           |
| `action_required`  | Instructor needs to fix      |
| `approved`         | Verified                     |
| `rejected`         | Denied                       |
| `expired`          | Expired                      |

### Error cases

**Non-admin user:**
**Expected 403:** Admin access required.

---

## 28. Admin — View Verification Detail

**GET** `/admin/1/`

> Replace `1` with the verification ID.

**Headers:**
```
Authorization: Bearer <admin_access_token>
```

**Expected 200:**
```json
{
    "success": true,
    "data": {
        "id": 1,
        "instructor_name": "Sarah Williams",
        "instructor_email": "sarah.instructor@example.com",
        "document_type": "national_id",
        "document_number": "AB1234567",
        "issuing_country": "Bangladesh",
        "expiry_date": "2030-12-31",
        "document_front": "/id_verification/documents/front/...",
        "document_back": null,
        "selfie": "/id_verification/selfies/...",
        "resume": null,
        "status": "submitted",
        "rejection_reason": "",
        "action_required_reason": "",
        "admin_notes": "",
        "reviewed_by_email": null,
        "reviewed_at": null,
        "created_at": "...",
        "submitted_at": "...",
        "updated_at": "..."
    }
}
```

---

## 29. Admin — Review Verification

**POST** `/admin/1/review/`

> Replace `1` with the verification ID.

**Headers:**
```
Authorization: Bearer <admin_access_token>
```

### 29a. Pick Up (submitted → under_review)

> Admin claims the request for review.

```json
{
    "action": "pick_up"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Verification under_review.",
    "data": {
        "id": 1,
        "status": "under_review",
        "reviewed_by_email": "admin@example.com",
        "reviewed_at": "...",
        "...other fields..."
    }
}
```

### 29b. Approve (under_review → approved)

> Approves the instructor's identity. This also sets `is_verified = True` on the instructor's profile.

```json
{
    "action": "approve",
    "admin_notes": "Documents verified. All clear."
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Verification approved.",
    "data": {
        "status": "approved",
        "...other fields..."
    }
}
```

### 29c. Reject (under_review → rejected)

> `rejection_reason` is **required** when rejecting.

```json
{
    "action": "reject",
    "rejection_reason": "Document image is blurry and unreadable.",
    "admin_notes": "Asked to resubmit with clearer photos."
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Verification rejected.",
    "data": {
        "status": "rejected",
        "rejection_reason": "Document image is blurry and unreadable.",
        "...other fields..."
    }
}
```

**Missing rejection_reason:**
```json
{
    "action": "reject"
}
```
**Expected 400:**
```json
{
    "success": false,
    "message": "Validation failed.",
    "errors": {
        "rejection_reason": "A reason is required when rejecting."
    }
}
```

### 29d. Request Action (under_review → action_required)

> Sends the request back to the instructor for corrections. `action_required_reason` is **required**.

```json
{
    "action": "request_action",
    "action_required_reason": "Selfie does not match the ID photo. Please retake.",
    "admin_notes": "Possible photo mismatch — give another chance."
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Verification action_required.",
    "data": {
        "status": "action_required",
        "action_required_reason": "Selfie does not match the ID photo. Please retake.",
        "...other fields..."
    }
}
```

**Missing action_required_reason:**
```json
{
    "action": "request_action"
}
```
**Expected 400:**
```json
{
    "success": false,
    "message": "Validation failed.",
    "errors": {
        "action_required_reason": "A reason is required when requesting action."
    }
}
```

### 29e. Expire a verification

```json
{
    "action": "expire"
}
```

**Expected 200:**
```json
{
    "success": true,
    "message": "Verification expired.",
    "data": { "status": "expired", "...other fields..." }
}
```

### Admin review — invalid transition errors

**Trying to approve a draft (not yet submitted):**
```json
{
    "action": "approve"
}
```
**Expected 400:** Cannot transition from "draft" to "approved". Allowed: submitted.

**Trying to pick up an already approved request:**
```json
{
    "action": "pick_up"
}
```
**Expected 400:** Cannot transition from "approved" to "under_review". Allowed: none (terminal state).

### Admin review — action options:
| Action            | Transitions From    | Transitions To      | Required Fields            |
|-------------------|---------------------|----------------------|----------------------------|
| `pick_up`         | submitted           | under_review         | —                          |
| `approve`         | under_review        | approved             | —                          |
| `reject`          | under_review        | rejected             | `rejection_reason`         |
| `request_action`  | under_review        | action_required      | `action_required_reason`   |
| `expire`          | submitted, under_review, action_required | expired | —               |

---

## Quick Test Flow — Instructor ID Verification (Full Cycle)

1. **Register** an instructor (step 2)
2. **Verify OTP** (step 5)
3. **Login** as the instructor (step 7) — save the `access` token
4. **Complete profile** (step 13b) — fill `headline`, `bio`, `specialization`, `years_of_experience`, `current_title`
5. **Create draft** (step 22a) — note the verification `id`
6. **Update** with document details (step 23a) — fill in type, number, country
7. **Upload documents** (step 23b) — use form-data for front image & selfie
8. *(Optional)* **Upload resume** (step 23b) — add resume/CV document
9. **Submit** (step 24) — transitions to `submitted` (fails if profile is incomplete)
10. **Login as admin** — save the admin `access` token
11. **List verifications** (step 27) with `?status=submitted` — find the request
12. **View detail** (step 28) — review the documents (including resume if provided)
13. **Pick up** (step 29a) — transitions to `under_review`
14. **Approve** (step 29b) — transitions to `approved`, instructor is now verified
15. **Login as instructor** again → **Get My Profile** (step 12) — confirm `is_verified: true`

## Quick Test Flow — Rejection & Resubmission

1. Complete steps 1–13 above (up to `under_review`)
2. **Reject** (step 29c) with a reason
3. **Login as instructor** → **List verifications** (step 25) — see `rejected` status with reason
4. Instructor creates a **new draft** (step 22) and goes through the flow again

## Quick Test Flow — Action Required & Resubmit

1. Complete steps 1–13 above (up to `under_review`)
2. **Request action** (step 29d) — admin specifies what needs to be fixed
3. **Login as instructor** → **List verifications** (step 25) — see `action_required` status and reason
4. **Update** the verification (step 23) — fix the issue (e.g., re-upload clearer document)
5. **Submit** again (step 24) — transitions back to `submitted` (profile completeness is checked again)
6. **Admin picks up** and **approves** (steps 29a, 29b)
