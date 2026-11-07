"""Settings shared by every environment."""

from pathlib import Path

import dj_database_url
import django_stubs_ext

from .env import env_list, env_str

# Allows `models.DecimalField[Any]`-style generics at runtime (django-stubs).
django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = env_str("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

# Bounded contexts, one app each (spec 01 §1.3). Order follows the dependency graph.
LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.vendors",
    "apps.catalog",
    "apps.tax",
    "apps.shipping",
    "apps.cart",
    "apps.orders",
    "apps.payments",
    "apps.ledger",
    "apps.ingest",
    "apps.cms",
    "apps.seo",
    "apps.compliance",
    "apps.api",
    "apps.mcp",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    *LOCAL_APPS,
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "mpmv.urls"
WSGI_APPLICATION = "mpmv.wsgi.application"
ASGI_APPLICATION = "mpmv.asgi.application"

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
            ],
        },
    },
]

# PostgreSQL is the only datastore (ADR-0001).
DATABASES = {
    "default": dj_database_url.parse(
        env_str("DATABASE_URL"), conn_max_age=60, conn_health_checks=True
    ),
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# English is the base language, Italian second, German ready (spec 09).
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("it", "Italiano"), ("de", "Deutsch")]
USE_I18N = True
LOCALE_PATHS = [BASE_DIR / "locale"]

# UTC in the database; Europe/Rome for schedules and documents (spec 01 conventions).
USE_TZ = True
TIME_ZONE = "UTC"
BUSINESS_TIME_ZONE = "Europe/Rome"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
