import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'career_college_backend.settings')

app = Celery('career_college_backend')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
