import os
import sys
from datetime import timedelta
from pathlib import Path

import environ

# True when running the Django test suite (e.g. `manage.py test`).
TESTING = 'test' in sys.argv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    SITE_ID=(int, 1),
    EMAIL_PORT=(int, 587),
    EMAIL_USE_TLS=(bool, True),
    CELERY_RESULT_EXPIRES=(int, 3600),
    ALLOWED_HOSTS=(list, ['127.0.0.1', 'localhost']),
)
environ.Env.read_env(BASE_DIR / '.env')


SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')

ALLOWED_HOSTS = env('ALLOWED_HOSTS')


# Application definition

INSTALLED_APPS = [
    # daphne must precede django.contrib.staticfiles to override runserver with ASGI
    'daphne',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.postgres',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_filters',
    'storages',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',

    # Real-time WebSocket
    'channels',

    # Local
    'authentication.apps.AuthenticationConfig',
    'id_verification',
    'courses.apps.CoursesConfig',
    'realtime.apps.RealtimeConfig',
    'notifications.apps.NotificationsConfig',
    'messaging.apps.MessagingConfig',
    'webinars.apps.WebinarsConfig',
    'analytics.apps.AnalyticsConfig',
    'payments.apps.PaymentsConfig',
    'admin_console.apps.AdminConsoleConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'career_college_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'career_college_backend.wsgi.application'
ASGI_APPLICATION = 'career_college_backend.asgi.application'


# Database
DATABASES = {
    'default': {
        'ENGINE': env('DB_ENGINE'),
        'NAME': env('DB_NAME'),
        'USER': env('DB_USER'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST', default='127.0.0.1'),
        'PORT': env('DB_PORT', default='5432'),
    }
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


AUTH_USER_MODEL = 'authentication.User'
SITE_ID = env.int('SITE_ID')

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
)

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'authentication.authentication.CookieJWTAuthentication',
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    # Rewraps DRF-raised errors (throttle/auth/permission/405/parse) in the
    # project's success/message envelope. Views build their own envelopes and
    # are unaffected. See core/exception_handlers.py.
    'EXCEPTION_HANDLER': 'core.exception_handlers.envelope_exception_handler',
}

# SimpleJWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=12),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# Google OAuth
GOOGLE_CLIENT_ID = env('GOOGLE_CLIENT_ID', default='') or env('GOOGLE_OAUTH_CLIENT_ID', default='')
GOOGLE_CLIENT_SECRET = env('GOOGLE_CLIENT_SECRET', default='') or env('GOOGLE_OAUTH_CLIENT_SECRET', default='')
GOOGLE_CALLBACK_URL = env('GOOGLE_CALLBACK_URL', default='http://localhost:8000/api/v1/auth/google/callback/')

# Frontend URLs
FRONTEND_URL = env('FRONTEND_URL', default='http://localhost:3000')
FRONTEND_GOOGLE_CALLBACK = env('FRONTEND_GOOGLE_CALLBACK', default='')
FRONTEND_ERROR_URL = env('FRONTEND_ERROR_URL', default='')
# Frontend path certificate verification URLs are built on (joined onto FRONTEND_URL).
# FRONTEND_URL must be the production domain in production — its value is printed
# on every issued certificate PDF and encoded in the QR code.
CERTIFICATE_VERIFY_PATH = env('CERTIFICATE_VERIFY_PATH', default='/verify/')

# SSLCommerz payment gateway
SSLCOMMERZ_STORE_ID = env('SSLCOMMERZ_STORE_ID', default='')
SSLCOMMERZ_STORE_PASSWORD = env('SSLCOMMERZ_STORE_PASSWORD', default='')
SSLCOMMERZ_SANDBOX = env.bool('SSLCOMMERZ_SANDBOX', default=True)
SSLCOMMERZ_BASE_URL = (
    'https://sandbox.sslcommerz.com' if SSLCOMMERZ_SANDBOX
    else 'https://securepay.sslcommerz.com'
)
# Backend base URL used to build the gateway callback URLs (success/fail/cancel/ipn).
BACKEND_URL = env('BACKEND_URL', default='http://localhost:8000')
# Frontend paths the payment callbacks redirect the browser to.
FRONTEND_PAYMENT_SUCCESS_PATH = env('FRONTEND_PAYMENT_SUCCESS_PATH', default='/payment/success')
FRONTEND_PAYMENT_FAIL_PATH = env('FRONTEND_PAYMENT_FAIL_PATH', default='/payment/fail')
FRONTEND_PAYMENT_CANCEL_PATH = env('FRONTEND_PAYMENT_CANCEL_PATH', default='/payment/cancel')

# CORS
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[FRONTEND_URL])
CORS_ALLOW_CREDENTIALS = True

# Keep allauth wired for SocialAccount model only
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': GOOGLE_CLIENT_ID,
            'secret': GOOGLE_CLIENT_SECRET,
            'key': '',
        },
        'SCOPE': ['openid', 'email', 'profile'],
        'AUTH_PARAMS': {
            'access_type': 'online',
            'prompt': 'select_account',
        },
    }
}

# JWT cookie settings (HttpOnly cookies for Google OAuth flow)
JWT_COOKIE_SECURE = env.bool('JWT_COOKIE_SECURE', default=not DEBUG)
JWT_COOKIE_SAMESITE = env('JWT_COOKIE_SAMESITE', default='Lax')
JWT_COOKIE_DOMAIN = env('JWT_COOKIE_DOMAIN', default='') or None
JWT_COOKIE_PATH = env('JWT_COOKIE_PATH', default='/')
JWT_ACCESS_COOKIE_NAME = env('JWT_ACCESS_COOKIE_NAME', default='access_token')
JWT_REFRESH_COOKIE_NAME = env('JWT_REFRESH_COOKIE_NAME', default='refresh_token')
# Admin-console session auth (scoped to /api/v1/admin-console/ views via
# per-view authentication_classes; deliberately NOT in the global DRF auth
# classes, so the JWT API keeps working without CSRF tokens).
ADMIN_SESSION_IDLE_TIMEOUT = env.int('ADMIN_SESSION_IDLE_TIMEOUT', default=1800)  # 30 min sliding
ADMIN_REAUTH_MAX_AGE = env.int('ADMIN_REAUTH_MAX_AGE', default=900)               # 15 min for sensitive actions
SESSION_COOKIE_AGE = ADMIN_SESSION_IDLE_TIMEOUT
SESSION_SAVE_EVERY_REQUEST = True            # sliding idle expiry (resets on each request)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env.bool('SESSION_EXPIRE_AT_BROWSER_CLOSE', default=True)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=not DEBUG)
SESSION_COOKIE_SAMESITE = env('SESSION_COOKIE_SAMESITE', default='Lax')
CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=not DEBUG)
CSRF_COOKIE_SAMESITE = env('CSRF_COOKIE_SAMESITE', default='Lax')
CSRF_COOKIE_HTTPONLY = False                 # JS must read csrftoken to send X-CSRFToken
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[FRONTEND_URL])

ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_USER_MODEL_USERNAME_FIELD = None

# Internationalization
LANGUAGE_CODE = 'en-us'

TIME_ZONE = env('TIME_ZONE')

USE_I18N = True

USE_TZ = True


# Static files
STATIC_URL = 'static/'
STATIC_ROOT = Path(env('STATIC_ROOT', default=str(BASE_DIR / 'staticfiles')))

# Media files (user uploads)
MEDIA_URL = env('MEDIA_URL', default='/media/')
MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))
FFMPEG_BINARY_PATH = env('FFMPEG_BINARY_PATH', default='ffmpeg')
FFPROBE_BINARY_PATH = env('FFPROBE_BINARY_PATH', default='ffprobe')

# Object storage (S3). Set AWS_STORAGE_BUCKET_NAME to switch default/staticfiles
# storage from local disk to S3 — used in production, unset for local dev.
AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME', default='')

if AWS_STORAGE_BUCKET_NAME:
    AWS_S3_CUSTOM_DOMAIN = env('AWS_S3_CUSTOM_DOMAIN', default='')
    AWS_LOCATION = env('AWS_LOCATION', default='media')
    AWS_S3_OBJECT_PARAMETERS = env.json('AWS_S3_OBJECT_PARAMETERS', default={'CacheControl': 'max-age=86400'})
    AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='')

    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
            'OPTIONS': {
                'bucket_name': AWS_STORAGE_BUCKET_NAME,
                'custom_domain': AWS_S3_CUSTOM_DOMAIN,
                'location': AWS_LOCATION,
                'object_parameters': AWS_S3_OBJECT_PARAMETERS,
                'default_acl': None,
                'querystring_auth': False,
                **({'region_name': AWS_S3_REGION_NAME} if AWS_S3_REGION_NAME else {}),
            },
        },
        'staticfiles': {
            'BACKEND': 'storages.backends.s3boto3.S3StaticStorage',
            'OPTIONS': {
                'bucket_name': AWS_STORAGE_BUCKET_NAME,
                'custom_domain': AWS_S3_CUSTOM_DOMAIN,
                'location': 'static',
            },
        },
    }
else:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }

# Celery
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default=CELERY_BROKER_URL)

# Django Channels — Redis channel layer.
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [env('CHANNEL_LAYERS_URL', default=CELERY_BROKER_URL)],
            'expiry': 60,
        },
    },
}

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE



CELERY_RESULT_EXPIRES = env.int('CELERY_RESULT_EXPIRES')
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ROUTES = {
    'notifications.tasks.send_notification_email_task': {'queue': 'notifications'},
    'notifications.tasks.purge_old_notifications_task': {'queue': 'notifications'},
}


if TESTING:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {
    'reap-stuck-coding-submissions': {
        'task': 'courses.tasks.reap_stuck_coding_submissions_task',
        'schedule': 60.0,
    },
    'expire-instructor-invites': {
        'task': 'courses.tasks.expire_instructor_invites_task',
        'schedule': 3600.0,  # hourly
    },
    'purge-old-notifications': {
        'task': 'notifications.tasks.purge_old_notifications_task',
        'schedule': 86400.0,  # daily
        'kwargs': {'days': 90},
    },
    'reap-stale-processing-orders': {
        'task': 'payments.tasks.reap_stale_processing_orders_task',
        'schedule': 900.0,  # every 15 min
    },
    'advance-course-schedules': {
        'task': 'courses.tasks.advance_course_schedules_task',
        'schedule': 300.0,  # every 5 min
    },
}

INSTRUCTOR_INVITE_EXPIRY_DAYS = env.int('INSTRUCTOR_INVITE_EXPIRY_DAYS', default=7)

# Coding-exercise Docker runner image overrides
RUNNER_IMAGE_PYTHON = env('RUNNER_IMAGE_PYTHON')
RUNNER_IMAGE_JAVASCRIPT = env('RUNNER_IMAGE_JAVASCRIPT')
RUNNER_IMAGE_CPP = env('RUNNER_IMAGE_CPP')
RUNNER_IMAGE_JAVA = env('RUNNER_IMAGE_JAVA')
RUNNER_RUNTIME = env('RUNNER_RUNTIME_DEV') if DEBUG else env('RUNNER_RUNTIME_PROD')


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Email settings
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS')
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_USE_SSL = False
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER)


# Rate Limiting
OTP_RATE_LIMIT = env('OTP_RATE_LIMIT', default='20/min')
# Shared login (LoginThrottle) also throttles admin logins — the admin console
# has no separate login endpoint; it rides POST /api/v1/auth/login/.
LOGIN_RATE_LIMIT = env('LOGIN_RATE_LIMIT', default='10/min')
# Per-admin cap on user-management mutations (suspend/reactivate/role).
ADMIN_ACTION_RATE_LIMIT = env('ADMIN_ACTION_RATE_LIMIT', default='30/min')
# Per-user cap on Q&A upvotes — the counters have no per-user vote row, so
# this is the only brake on one caller inflating a thread's ranking.
DISCUSSION_UPVOTE_RATE_LIMIT = env('DISCUSSION_UPVOTE_RATE_LIMIT', default='30/min')
# Per-user cap on AI outline generation. Unlike the throttles above this one
# guards real spend — every call is a paid LLM request taking several seconds.
AI_OUTLINE_RATE_LIMIT = env('AI_OUTLINE_RATE_LIMIT', default='10/min')
# Same reason, separate counter: article drafting happens once per lesson while
# outlining happens once per course, so sharing a scope would let one exhaust
# the other's budget.
AI_ARTICLE_RATE_LIMIT = env('AI_ARTICLE_RATE_LIMIT', default='10/min')
# Same reason again, third counter: questions are generated per quiz, so sharing
# a scope with outlining or article drafting would let one exhaust the others.
AI_QUIZ_RATE_LIMIT = env('AI_QUIZ_RATE_LIMIT', default='10/min')
AI_CODING_RATE_LIMIT = env('AI_CODING_RATE_LIMIT', default='10/min')


# AI services (FastAPI project — hosts every AI feature, not just outlines).
# Server-to-server only; the provider API key lives there, never here.
AI_SERVICES_BASE_URL = env('AI_SERVICES_BASE_URL', default='http://localhost:8001')
AI_SERVICES_KEY = env('AI_SERVICES_KEY', default='')


# Logging
LOG_DIR = Path(env('LOG_DIR', default=str(BASE_DIR / 'logs')))
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE_PATH = LOG_DIR / 'app.log'

def _is_log_file_writable(path: Path) -> bool:
    try:
        with path.open('a', encoding='utf-8'):
            pass
        return True
    except OSError:
        return False

# `runserver` spawns a watcher process plus a reloaded child (RUN_MAIN=true).
# Both import settings.py, so without this check both processes would open
# app.log and race RotatingFileHandler's rename on rollover — fatal only on
# Windows, where you can't rename a file another process has open.
IS_RELOADER_CHILD = os.environ.get('RUN_MAIN') == 'true'
FILE_LOGGING_ENABLED = (not DEBUG or IS_RELOADER_CHILD) and _is_log_file_writable(LOG_FILE_PATH)
DEFAULT_LOG_HANDLERS = ['console', 'file'] if FILE_LOGGING_ENABLED else ['console']

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOG_FILE_PATH),
            'maxBytes': 5 * 1024 * 1024,  # 5 MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'authentication': {
            'handlers': DEFAULT_LOG_HANDLERS,
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': DEFAULT_LOG_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
        'django': {
            'handlers': DEFAULT_LOG_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
        'notifications': {
            'handlers': DEFAULT_LOG_HANDLERS,
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'realtime': {
            'handlers': DEFAULT_LOG_HANDLERS,
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'analytics': {
            'handlers': DEFAULT_LOG_HANDLERS,
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
    'root': {
        'handlers': DEFAULT_LOG_HANDLERS,
        'level': 'INFO',
    },
}
