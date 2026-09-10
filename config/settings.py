"""Django settings for the Smart Attendance System.

Every tunable below reads from the environment so that thresholds, model paths
and SMTP credentials can be changed without touching code.
"""
from __future__ import annotations

from pathlib import Path

from .env import env_bool, env_float, env_int, env_list, env_str, load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["*"] if DEBUG else [])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.accounts",
    "apps.academics",
    "apps.students",
    "apps.attendance",
    "apps.face",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.ForcePasswordChangeMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --------------------------------------------------------------------------
# Database — SQLite only, embeddings stored as BLOBs.
# --------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env_str("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {"timeout": 30},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": env_int("MIN_PASSWORD_LENGTH", 8)}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:post_login"
LOGOUT_REDIRECT_URL = "accounts:login"

SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 12)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", False)

LANGUAGE_CODE = env_str("DJANGO_LANGUAGE_CODE", "en-us")
TIME_ZONE = env_str("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env_str("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))

# --------------------------------------------------------------------------
# Face recognition pipeline
# --------------------------------------------------------------------------
# Model
FACE_MODEL_NAME = env_str("FACE_MODEL_NAME", "buffalo_l")
FACE_MODEL_ROOT = env_str("FACE_MODEL_ROOT", str(BASE_DIR / "models"))
FACE_MODEL_PROVIDERS = env_list("FACE_MODEL_PROVIDERS", ["CPUExecutionProvider"])
FACE_DET_SIZE = env_int("FACE_DET_SIZE", 640)
FACE_EMBEDDING_DIM = env_int("FACE_EMBEDDING_DIM", 512)

# Matching thresholds — nothing in the matcher is magic-numbered.
SIMILARITY_THRESHOLD = env_float("SIMILARITY_THRESHOLD", 0.45)
MARGIN_THRESHOLD = env_float("MARGIN_THRESHOLD", 0.05)
MIN_DETECTION_SCORE = env_float("MIN_DETECTION_SCORE", 0.50)
MIN_FACE_PIXELS = env_int("MIN_FACE_PIXELS", 40)

# Enrollment quality gates
ENROLLMENT_STEPS = env_int("ENROLLMENT_STEPS", 5)
MIN_ENROLL_FACE_PIXELS = env_int("MIN_ENROLL_FACE_PIXELS", 120)
MIN_ENROLL_DET_SCORE = env_float("MIN_ENROLL_DET_SCORE", 0.65)
MIN_ENROLL_BLUR_VARIANCE = env_float("MIN_ENROLL_BLUR_VARIANCE", 45.0)
MIN_ENROLL_BRIGHTNESS = env_float("MIN_ENROLL_BRIGHTNESS", 45.0)
MAX_ENROLL_BRIGHTNESS = env_float("MAX_ENROLL_BRIGHTNESS", 235.0)
DUPLICATE_EMBEDDING_THRESHOLD = env_float("DUPLICATE_EMBEDDING_THRESHOLD", 0.98)

# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------
MAX_UPLOAD_SIZE_MB = env_int("MAX_UPLOAD_SIZE_MB", 12)
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = env_list(
    "ALLOWED_IMAGE_CONTENT_TYPES",
    ["image/jpeg", "image/png", "image/webp"],
)
MAX_IMAGE_DIMENSION = env_int("MAX_IMAGE_DIMENSION", 2400)
REENCODE_JPEG_QUALITY = env_int("REENCODE_JPEG_QUALITY", 90)
MAX_SESSION_IMAGES = env_int("MAX_SESSION_IMAGES", 3)
MIN_SESSION_IMAGES = env_int("MIN_SESSION_IMAGES", 2)

# --------------------------------------------------------------------------
# Timetable
# --------------------------------------------------------------------------
PERIODS_PER_DAY = env_int("PERIODS_PER_DAY", 8)

# When true a teacher may only take attendance for the section/subject pairs in
# their TeacherAssignment rows. When false any teacher may take any active
# class; they still only see and process the sessions they recorded themselves.
RESTRICT_TEACHER_TO_ASSIGNMENTS = env_bool("RESTRICT_TEACHER_TO_ASSIGNMENTS", False)

# Rows below this percentage are flagged in the reports.
LOW_ATTENDANCE_PERCENT = env_float("LOW_ATTENDANCE_PERCENT", 75.0)

# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------
EMAIL_BACKEND = env_str(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env_str("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 20)
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL", "Smart Attendance <no-reply@example.edu>")
ATTENDANCE_EMAIL_CC = env_list("ATTENDANCE_EMAIL_CC", [])
INSTITUTION_NAME = env_str("INSTITUTION_NAME", "Smart Attendance")

# --------------------------------------------------------------------------
# Logging — the pipeline logs image/face/match counts and elapsed seconds.
# --------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_LEVEL = env_str("LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "attendance.log"),
            "maxBytes": 2 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "apps": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
    },
}

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
    CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
