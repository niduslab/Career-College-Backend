# Postman Testing Guide — Registration, Login, Forgot Password, Reset Password & Profiles

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
| `profile_photo` | Text | *(leave empty)* |

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

- Set **Content-Type** to `application/json` on all requests
- Create an environment variable `base_url` = `http://127.0.0.1:8000/api/v1/auth`
- After login, save `access` token to environment and use `Bearer {{access_token}}` in the Authorization tab for authenticated endpoints
