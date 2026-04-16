# Career College Backend

Backend API project built with Django and Django REST Framework (DRF).

## Tech Stack

- Django 5
- Django REST Framework
- Simple JWT (authentication)
- django-filter
- django-cors-headers
- python-dotenv

## Prerequisites

- Python 3.14+
- pip

## Project Setup

1. Clone the repository and open it in your terminal.
2. Run setup commands in Bash.

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Git Bash (Windows)
source .venv/Scripts/activate

# macOS/Linux Bash
# source .venv/bin/activate

# Install dependencies
python -m pip install -r requirements.txt

# Apply migrations
python manage.py migrate

# Run development server
python manage.py runserver
```

## .env Setup

1. Create a local environment file from the template:

```bash
cp .env.example .env
```

2. Open .env and set required values:

- `SECRET_KEY`: use a long random string unique to your machine.
- `DEBUG`: use `True` for local development.
- `ALLOWED_HOSTS`: keep `127.0.0.1,localhost` for local development.
- `DB_ENGINE` and `DB_NAME`: keep defaults for SQLite unless you are using another database.
- `EMAIL_*` and `DEFAULT_FROM_EMAIL`: set your SMTP details if testing real email delivery.
- `OTP_RATE_LIMIT`: keep default unless you need stricter/looser local limits.

3. Local email testing options:

- Option 1 (real email): keep SMTP values from `.env.example` and fill credentials.
- Option 2 (no real email): set `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` to print OTP/email content in terminal.

4. Never commit `.env`; only commit `.env.example` when keys change.

## Base API URLs

- **Auth:** `http://127.0.0.1:8000/api/v1/auth`
- **Verification:** `http://127.0.0.1:8000/api/v1/verification`

## Core Features

### Authentication & Authorization
- Email-based user registration (no username field)
- OTP email verification for registration and password reset
- JWT token-based authentication (access + refresh tokens)
- Three user types: Learner, Instructor, Partner Institution
- Role-based access control with custom permissions

### Profile Management
- User profiles specific to each type (Learner, Instructor, Partner Institution)
- Education and work experience entries (learners & instructors)
- Public profile browsing with filtering (by country, experience level, verification status)
- Profile visibility controls (private/public)
- File uploads (profile photo, institution logo/cover, resume)

### Instructor Identity Verification
- Multi-stage verification workflow: draft → submitted → under_review → approved/rejected/action_required
- Support for multiple ID document types (national ID, passport, driver's license, residence permit)
- Document image uploads (front, back, selfie)
- Optional resume/CV upload
- Admin review system with approval, rejection, and action-requested states
- Automatic instructor profile verification flag on approval
- Resubmission workflow for fixing rejected documents

### Soft Deletion
- User soft delete with soft_delete reason tracking
- Admin can view/manage soft-deleted users
- API filters out deleted users by default

## Auth Endpoints

- **POST** `/register/` — Register new user (learner, instructor, or partner institution)
- **POST** `/login/` — Login with email & password
- **POST** `/token/refresh/` — Refresh access token (requires refresh token)
- **POST** `/logout/` — Logout (requires refresh token)
- **POST** `/otp/verify/` — Verify OTP for registration or password reset
- **POST** `/otp/resend/` — Resend OTP
- **POST** `/password/forgot/` — Request password reset OTP
- **POST** `/password/reset/` — Reset password with token
- **POST** `/password/change/` — Change password (authenticated)

## Profile Endpoints

- **GET** `/profile/me/` — Get authenticated user's profile
- **PATCH** `/profile/me/` — Update profile (supports form-data for file uploads)
- **GET** `/profiles/<slug>/` — View public profile by slug (no auth required)
- **GET** `/profiles/learners/` — Browse public learner profiles (paginated, filterable)
- **GET** `/profiles/instructors/` — Browse public instructor profiles (paginated, filterable)
- **GET** `/profiles/institutions/` — Browse public institution profiles (paginated, filterable)

### Education & Work Experience

- **GET** `/profile/me/education/` — List education entries
- **POST** `/profile/me/education/` — Create education entry
- **PATCH** `/profile/me/education/<id>/` — Update education entry
- **DELETE** `/profile/me/education/<id>/` — Delete education entry
- **GET** `/profile/me/work-experience/` — List work experience entries
- **POST** `/profile/me/work-experience/` — Create work experience entry
- **PATCH** `/profile/me/work-experience/<id>/` — Update work experience entry
- **DELETE** `/profile/me/work-experience/<id>/` — Delete work experience entry

## Verification Endpoints (Instructors)

- **POST** `/create/` — Create draft verification request
- **PATCH** `/<id>/update/` — Update draft/action_required verification
- **POST** `/<id>/submit/` — Submit verification for admin review
- **GET** `/my/` — List instructor's verification requests
- **GET** `/my/<id>/` — View specific verification request

## Verification Endpoints (Admin)

- **GET** `/admin/list/` — List all verification requests (paginated, filterable by status)
- **GET** `/admin/<id>/` — View verification request detail
- **POST** `/admin/<id>/review/` — Review verification (pick_up, approve, reject, request_action, expire)

## Admin Features

### User Management
- Custom UserAdmin with email-based login (no username)
- View and manage soft-deleted users
- Education and work experience inlines
- Verification request tracking

### Verification Management
- List all instructor verification requests with status filtering
- Review documents (images, resume)
- Approve verified instructors (auto-sets `is_verified` flag)
- Reject with detailed reason
- Request action with specific feedback for resubmission
- Expire stale verification requests
- Internal admin notes

## Error Handling

All endpoints include comprehensive error handling:
- **Try/except blocks** on database operations (save, delete)
- **ObjectDoesNotExist handling** for reverse OneToOneField accessors
- **Consistent error response format** with `success`, `message`, and `errors` fields
- **HTTP status codes**: 400 (validation), 401 (auth), 403 (permission), 404 (not found), 500 (server error)

## Forgot Password Flow

1. Call **POST** `/password/forgot/` with user email
2. Verify OTP via **POST** `/otp/verify/` using `purpose: "password_reset"`
3. API returns `reset_token` in the response
4. Call **POST** `/password/reset/` with:
   - `email`
   - `reset_token` (from step 3)
   - `new_password`
   - `confirm_password`

**Note:** The `reset_token` is system-generated and sent in the OTP verify response — not typed by the user.

## Testing & Documentation

Comprehensive testing guide with curl/Postman examples for all endpoints: **[POSTMAN_TESTING_GUIDE.md](POSTMAN_TESTING_GUIDE.md)**

Includes:
- Authentication flows (registration, OTP, login, logout)
- Password reset flow
- Profile management (create, read, update)
- Education and work experience CRUD
- Public profile browsing
- **Instructor ID verification workflow** (full cycle with quick test flows)
- Admin verification review actions
- Error cases for each endpoint

### Admin Setup for Testing

Create a superuser (admin account for verification review):
```bash
python manage.py createsuperuser
```

**Important:** Superusers are automatically `is_email_verified=True` and can log in without OTP verification. Existing superusers in the database have been updated automatically.

## Environment Variables

Configured in .env:

- SECRET_KEY
- DEBUG
- ALLOWED_HOSTS
- DB_ENGINE
- DB_NAME
- EMAIL_BACKEND
- EMAIL_HOST
- EMAIL_PORT
- EMAIL_USE_TLS
- EMAIL_HOST_USER
- EMAIL_HOST_PASSWORD
- DEFAULT_FROM_EMAIL
- OTP_RATE_LIMIT

Sample values are provided in .env.example.

## Useful Commands

```bash
# Create migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Create superuser (admin account)
python manage.py createsuperuser

# Run Django shell
python manage.py shell

# Run tests
python manage.py test

# Check code for errors
python manage.py check

# View available commands
python manage.py help
```

### Quick Database Queries via Shell

```bash
# View OTP for testing
python manage.py shell -c "from auth.models import User; u=User.objects.get(email='user@example.com'); print(u.otp_code)"

# Mark all superusers as email verified
python manage.py shell -c "from auth.models import User; User.objects.all_with_deleted().filter(is_superuser=True).update(is_email_verified=True)"

# View soft-deleted users
python manage.py shell -c "from auth.models import User; print(User.objects.deleted_only().count())"
```

## Project Structure

```
career_college_backend/
├── auth/                          # User authentication & profiles
│   ├── models.py                  # Custom User model, profiles (Learner, Instructor, PartnerInstitution)
│   ├── serializers.py             # Serializers for auth endpoints
│   ├── views.py & all_views/      # Auth viewsets (registration, login, OTP, password reset)
│   ├── utils/                     # Email utilities, password validators
│   └── migrations/                # Database migrations
├── id_verification/               # Instructor identity verification
│   ├── models.py                  # IdentityVerification model (state machine)
│   ├── serializers.py             # Serializers for instructor & admin endpoints
│   ├── views.py & all_views/      # Instructor & admin viewsets
│   ├── admin.py                   # Django admin customization
│   └── migrations/                # Database migrations
├── core/                          # Shared utilities
│   ├── pagination.py              # StandardResultsSetPagination
│   ├── permissions.py             # Custom permission classes
│   └── middleware.py              # Custom middleware
├── templates/                     # Email templates
├── manage.py                      # Django management script
├── requirements.txt               # Python dependencies
├── .env.example                   # Template for environment variables
└── POSTMAN_TESTING_GUIDE.md       # Complete API testing guide
```

## Key Models

### User Model
- Custom user model with email as unique identifier (no username field)
- User types: learner, instructor, partner_institution, admin
- Soft delete support (is_deleted, deleted_at, deletion_reason)
- OTP tracking (otp_code, otp_created_at, otp_purpose, otp_verified)
- Password reset token management
- Email verification flag

### Profile Models
- **LearnerProfile**: education goals, interests, profile visibility
- **InstructorProfile**: specialization, experience, verification status
- **PartnerInstitutionProfile**: institution details, logo, cover image, active status

### IdentityVerification Model
- State machine with validated transitions
- Document tracking: type, number, issuing country, expiry date
- File uploads: document front/back, selfie, resume
- Admin review: reviewer, review date, rejection reason, action required reason
- Timestamps: created, submitted, reviewed, updated

## Database

Default: SQLite (`db.sqlite3`)

For production, update `DB_ENGINE` and `DB_NAME` in `.env`:
```bash
DB_ENGINE=django.db.backends.postgresql
DB_NAME=your_database_name
```

## Notes & Best Practices

- **Keep .env private** — never commit it; use `.env.example` as the shared template
- **Email testing locally** — use console backend in `.env.example` to print OTP in terminal
- **OTP for testing** — check database or terminal output: `User.otp_code`
- **Admin users** — created via `createsuperuser` are auto-verified and can log in immediately
- **Soft deletes** — users deleted via API remain in database with `is_deleted=True`; use `User.objects.all_with_deleted()` in admin queries
- **File uploads** — always use form-data (not JSON) for endpoints accepting files
- **Verification workflow** — instructors can only have one active verification request (draft/submitted/under_review/action_required)
- **Instructor verification** — documents required: document_type, document_number, issuing_country, document_front, selfie; optional: document_back, expiry_date, resume
- **Email templates** — located in `templates/emails/`; customize branding, sender, content as needed
- **Error handling** — all API errors return consistent format with `success: false`, `message`, and `errors` fields
