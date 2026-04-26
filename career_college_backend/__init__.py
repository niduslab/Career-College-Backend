try:
    from career_college_backend.celery import app as celery_app
except Exception:  # pragma: no cover
    celery_app = None

__all__ = ['celery_app']
