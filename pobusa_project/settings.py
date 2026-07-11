# pobusa_project/settings.py — v1.1.0
# v1.1.0: switched from EMAIL_USE_TLS to EMAIL_USE_SSL — port 465 (cPanel's
# recommended SMTP port) is SSL, not TLS. Using the wrong one causes the
# connection to silently misbehave rather than give a clear error.

import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# --- SECURITY ---
# For local dev this default is fine. Before deploying to Railway, set a
# real SECRET_KEY env var and replace the fallback below.
SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-CHANGE-THIS-BEFORE-DEPLOYING-dev-only-key-not-safe-for-production"
)

DEBUG = os.environ.get("DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# --- APPLICATIONS ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "pobusa",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pobusa_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "pobusa_project.wsgi.application"

# --- DATABASE ---
# Uses DATABASE_URL from Railway automatically once deployed. For local dev
# without a DATABASE_URL set, falls back to local sqlite so `python manage.py
# check` and early testing work without needing Postgres running yet.
if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.config(default=os.environ.get("DATABASE_URL"), conn_max_age=600)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --- PASSWORD VALIDATION ---
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- INTERNATIONALIZATION ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Johannesburg"
USE_I18N = True
USE_TZ = True

# --- STATIC FILES ---
STATIC_URL = "static/"

# --- MEDIA FILES ---
# Store.logo and DailyReportFile.pdf_file use this. Local filesystem for now
# — note this is EPHEMERAL on Railway (wiped on every redeploy). Fine for
# the GG's Trading Card Store test run; needs to move to persistent storage
# (e.g. Cloudflare R2, same pattern as PokeBulk SA's card images) before
# relying on this long-term.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- REST FRAMEWORK ---
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",  # tightened per-view where needed (see permissions.py)
    ],
}

# --- CORS ---
# Frontend calls this API from a different domain/port — CORS must allow it.
CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000"  # local Next.js dev server default
).split(",")

# --- EMAIL (manual send action — see email_service.py) ---
# Port 465 = SSL. Port 587 = TLS. Using the right one for your provider matters —
# cPanel-hosted mail (pokebulk.co.za) typically uses 465/SSL.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 465))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_SSL = True
EMAIL_USE_TLS = False
