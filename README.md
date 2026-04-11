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
2. Create a virtual environment:

   python -m venv .venv

3. Activate the virtual environment:

   Windows (PowerShell):
   .\.venv\Scripts\Activate.ps1

   Git Bash:
   source .venv/Scripts/activate

4. Install dependencies:

   python -m pip install -r requirements.txt

5. Create environment file from sample:

   Copy .env.example to .env

6. Apply migrations:

   python manage.py migrate

7. Run the development server:

   python manage.py runserver

## Base API URL

- http://127.0.0.1:8000/api/v1/auth

## Auth Endpoints

- POST /register/
- POST /login/
- POST /logout/
- POST /otp/verify/
- POST /otp/resend/
- POST /password/forgot/
- POST /password/reset/
- POST /password/change/

Detailed payloads and response examples are documented in POSTMAN_TESTING_GUIDE.md.

## Forgot Password Flow (Current Implementation)

1. Call POST /password/forgot/ with user email.
2. Verify OTP via POST /otp/verify/ using purpose=password_reset.
3. API returns a reset_token in the OTP verify response.
4. Call POST /password/reset/ with:
   - email
   - reset_token
   - new_password
   - confirm_password

Note: reset_token is system-generated and should be sent by frontend code (not typed by user).

Reset password success response includes:
- data.email
- data.user_id
- data.user_slug

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

- Create migrations:
  python manage.py makemigrations

- Apply migrations:
  python manage.py migrate

- Create superuser:
  python manage.py createsuperuser

- Run checks:
  python manage.py check

## Notes

- Keep .env private and never commit it.
- Use .env.example as the shared template for your team.
- Email OTP templates are loaded from templates/emails/.
