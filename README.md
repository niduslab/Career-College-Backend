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

   python -m venv venv

3. Activate the virtual environment:

   Windows (PowerShell):
   .\venv\Scripts\Activate.ps1

4. Install dependencies:

   python -m pip install -r requirements.txt

5. Create environment file from sample:

   Copy .env.example to .env

6. Apply migrations:

   python manage.py migrate

7. Run the development server:

   python manage.py runserver

## Environment Variables

Configured in .env:

- SECRET_KEY
- DEBUG
- ALLOWED_HOSTS
- DB_ENGINE
- DB_NAME

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
