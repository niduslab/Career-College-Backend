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
   ```bash
   python manage.py makemigrations
   ```

- Apply migrations:
   ```bash
   python manage.py migrate
   ```

- Create superuser:
   ```bash
   python manage.py createsuperuser
   ```

- Run checks:
   ```bash
   python manage.py check
   ```

## Notes

- Keep .env private and never commit it.
- Use .env.example as the shared template for your team.
- Email OTP templates are loaded from templates/emails/.
