# Postman Testing Guide — Registration, Login, Forgot Password & Reset Password

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

## Postman Tips

- Set **Content-Type** to `application/json` on all requests
- Create an environment variable `base_url` = `http://127.0.0.1:8000/api/v1/auth`
- After login, save `access` token to environment and use `Bearer {{access_token}}` in the Authorization tab for authenticated endpoints
