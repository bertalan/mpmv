"""Settings shared by every environment."""

from pathlib import Path

import dj_database_url
import django_stubs_ext
from django.utils.csp import CSP

from .env import env_bool, env_flags, env_list, env_secret_list, env_str

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
    "apps.themes",  # frontend themes (THM.1, spec 14): imports only core and Wagtail
    "apps.seo",
    "apps.compliance",
    "apps.api",
    "apps.mcp",
]

INSTALLED_APPS = [
    # Django's admin with the self-service password change audited (SEC.4).
    "apps.accounts.admin_apps.AuditedAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # range fields and exclusion constraints (validity periods)
    # Task queue tables, admin and `manage.py procrastinate`; used only via apps.core.tasks.
    "procrastinate.contrib.django",
    # API v1 (CORE.7): serves the Swagger UI from local static files, without inline scripts.
    "ninja",
    # Second factors (CORE.4): device framework and TOTP devices; passkeys and recovery codes
    # are accounts models. Listed before accounts so its admin can unregister the raw device admin.
    "django_otp",
    "django_otp.plugins.otp_totp",
    # Wagtail (CMS.1, ADR-0001): pages, snippets, images (I18N.3) and the editors' admin. No
    # embeds, forms or redirects yet: later CMS tasks add them when a page type needs them.
    # Documents only because wagtail-localize's editor imports their models; its admin menu is
    # hidden (apps.cms.wagtail_hooks) until a page type needs documents.
    "wagtail.sites",
    "wagtail.users",
    "wagtail.snippets",
    "wagtail.images",
    "wagtail.documents",
    "wagtail.search",
    "wagtail.admin",
    "wagtail.contrib.settings",  # BaseSiteSetting for the per-site theme (THM.1)
    "wagtail",
    "modelcluster",
    "taggit",
    *LOCAL_APPS,
    # Translation of CMS pages and snippets (I18N.3, spec 09). After the local apps, so the
    # CSP-safe copies of its admin templates in apps/cms/templates take precedence.
    "wagtail_localize",
]

# Background jobs (ADR-0004): cron schedules are read in BUSINESS_TIME_ZONE by this hook.
PROCRASTINATE_ON_APP_READY = "apps.core.tasks.on_app_ready"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "apps.cms.middleware.WagtailAdminPickerNonceMiddleware",  # Wagtail's inline pickers (CMS.1)
    "apps.core.middleware.PermissionsPolicyMiddleware",
    "apps.themes.middleware.ThemeMiddleware",  # the site's theme for the template loader (THM.1)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    # CAT.5: a 404 on a product's old slug becomes a 301 to its current slug.
    "apps.catalog.middleware.OldProductSlugRedirectMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # CORE.4: which device verified the session, then confine required roles to the MFA pages.
    "django_otp.middleware.OTPMiddleware",
    "apps.accounts.middleware.MfaRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Custom user from the first migration (CORE.1): email login, preferred language.
AUTH_USER_MODEL = "accounts.User"
# Lockout and captcha checks run first, before any password is checked (CORE.5).
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.LoginProtectionBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Multi-factor authentication (CORE.4, spec 02 §2.2). Staff and superusers must verify a second
# factor (TOTP or passkey) before any page; each dotted path below names an extra
# `check(user) -> bool` that makes MFA mandatory for more roles. Every vendor member, owner or
# staff, must use MFA (owner decision Q-09).
MFA_REQUIRED_POLICIES: list[str] = [
    "apps.vendors.services.is_vendor_member",  # all vendor members (CORE.2)
    "apps.cms.services.can_access_cms_admin",  # Wagtail editors, staff or not (CMS.1)
]
MFA_STEP_UP_MAX_AGE = 300  # seconds a verification counts as fresh for sensitive actions
# Step-up for sensitive views of apps that cannot import accounts (the core audit log view, SEC.4).
STEP_UP_DECORATOR = "apps.accounts.decorators.step_up_required"
MFA_LOGIN_URL = "admin:login"  # until the site and vendor login pages exist
MFA_ISSUER = "MPMV"  # name shown in authenticator apps and passkey prompts
OTP_TOTP_ISSUER = MFA_ISSUER
OTP_ADMIN_HIDE_SENSITIVE_DATA = True

# Vendor deletion (CORE.14): `vendors` cannot import the apps above it (spec 01 §1.3). Each dotted
# path names a `hook(vendor, changes)` that adds one delete to the deletion's ChangeSet per row
# the deletion cascades to, so every such row gets its own AuditLog entry.
VENDOR_DELETION_AUDIT_HOOKS: list[str] = [
    "apps.api.services.vendor_key_deletions",  # the vendor's API keys (CORE.13)
]

ROOT_URLCONF = "mpmv.urls"
WSGI_APPLICATION = "mpmv.wsgi.application"
ASGI_APPLICATION = "mpmv.asgi.application"

# Templates (spec 14 §14.3): the active theme and its parents up to `base` first, then the
# project and app directories. The theme loader caches per (theme, name) itself; Django's
# cached.Loader (keyed by name only) wraps only the theme-independent loaders.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.csp",
                "apps.themes.context_processors.theme",
            ],
            "loaders": [
                "apps.themes.loaders.ThemeLoader",
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                ),
            ],
        },
    },
]

# Frontend themes (THM.1, spec 14): one directory per theme, each with a theme.toml manifest.
# A Wagtail Site's ThemeSettings picks its theme; MPMV_THEME_DEFAULT is the fallback (system
# check themes.E002 fails when it names no registered theme), then `base`.
MPMV_THEMES_DIR = BASE_DIR / "themes"
MPMV_THEME_DEFAULT = env_str("MPMV_THEME_DEFAULT", "") or "base"

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

# Language order (spec 09, D-18): EN primary and source, then IT, DE, FR, ES; more are added here.
LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("it", "Italiano"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
]
USE_I18N = True
LOCALE_PATHS = [BASE_DIR / "locale"]

# Wagtail (CMS.1, spec 10). Page locales follow LANGUAGES: apps.cms creates a Locale row for each
# language after every migrate. The editors' admin is at /admin/ (mpmv/urls.py).
WAGTAIL_SITE_NAME = "MPMV"
WAGTAIL_I18N_ENABLED = True
WAGTAIL_CONTENT_LANGUAGES = LANGUAGES
# The public address of the site, in Wagtail's admin emails and previews. The localhost fallback
# is for a developer machine: `check --deploy` fails on LIVE and STAGE without it (cms.E001).
WAGTAILADMIN_BASE_URL = env_str("MPMV_BASE_URL", "") or "http://localhost:8000"
# One image model for CMS pages, products (CAT.4), vendor logos and banners (spec 10 §10.2). Set
# before any image exists: the licence and credit fields come with CMS.8.
WAGTAILIMAGES_IMAGE_MODEL = "core.MediaImage"
# wagtail-localize (I18N.3): translations follow their source page; a translator syncs them from
# the source page's "Sync translated pages" action.
WAGTAIL_LOCALIZE_DEFAULT_TRANSLATION_MODE = "synced"
WAGTAILSEARCH_BACKENDS = {"default": {"BACKEND": "wagtail.search.backends.database"}}
# Passwords, email addresses, users and groups are managed where changes are audited (SEC.4),
# not from Wagtail's account and user pages.
WAGTAIL_PASSWORD_MANAGEMENT_ENABLED = False
WAGTAIL_PASSWORD_RESET_ENABLED = False
WAGTAIL_EMAIL_MANAGEMENT_ENABLED = False
WAGTAILUSERS_PASSWORD_ENABLED = False
# No third-party requests from the admin (CSP, privacy); upgrades come through uv.lock.
WAGTAIL_GRAVATAR_PROVIDER_URL = None
WAGTAIL_ENABLE_UPDATE_CHECK = False

# UTC in the database; Europe/Rome for schedules and documents (spec 01 conventions).
USE_TZ = True
TIME_ZONE = "UTC"
BUSINESS_TIME_ZONE = "Europe/Rome"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# HTTP hardening shared by every environment (SEC.1, spec 12 SEC.3). Transport-only settings
# (HSTS, HTTPS redirect, Secure cookies) live in live.py, since dev runs over plain HTTP.
# Strict CSP without 'unsafe-inline': inline code needs the per-request nonce
# ({% csp_nonce_attr %} or {{ csp_nonce }} in templates).
SECURE_CSP: dict[str, list[str] | bool] = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE],
    "style-src": [CSP.SELF, CSP.NONCE],
    "img-src": [CSP.SELF],
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.NONE],
    "frame-ancestors": [CSP.NONE],
    "form-action": [CSP.SELF],
}

# Client IP for throttles, audit and allow-lists (CORE.5, apps.core.http.client_ip): the header the
# reverse proxy overwrites with the client address, read only from TRUSTED_PROXIES (addresses or
# CIDR networks). Empty: REMOTE_ADDR, right when nothing sits in front of the app.
TRUSTED_PROXY_HEADER = env_str("MPMV_TRUSTED_PROXY_HEADER", "")
TRUSTED_PROXIES = env_list("MPMV_TRUSTED_PROXIES")

# Bot protection on login and registration forms (CORE.5): Cloudflare Turnstile behind
# apps.accounts.captcha.CaptchaVerifier. Off unless explicitly enabled; keys from the environment
# (a system check fails when it is enabled without them).
CAPTCHA_ENABLED = env_bool("MPMV_CAPTCHA_ENABLED", default=False)
CAPTCHA_VERIFIER = "apps.accounts.captcha.TurnstileVerifier"
TURNSTILE_SITE_KEY = env_str("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = env_str("TURNSTILE_SECRET_KEY", "")
if CAPTCHA_ENABLED:
    _TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"
    SECURE_CSP = {
        **SECURE_CSP,
        "script-src": [*SECURE_CSP["script-src"], _TURNSTILE_ORIGIN],  # type: ignore[misc]
        "frame-src": [_TURNSTILE_ORIGIN],
    }

# VIES VAT number checks (TAX.5): the EU REST API behind apps.tax.vies.ViesClient, asked once per
# check with a timeout (retries only in the tax.recheck_vat_number job). Valid and invalid answers
# are reused for VIES_CACHE_SECONDS, "VIES down" only for VIES_UNAVAILABLE_CACHE_SECONDS.
VIES_CLIENT = "apps.tax.vies.HttpViesClient"
VIES_API_URL = env_str(
    "MPMV_VIES_API_URL", "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
)
VIES_TIMEOUT_SECONDS = 8  # per socket operation (connect, each read), not for the whole check
VIES_CACHE_SECONDS = 24 * 60 * 60
VIES_UNAVAILABLE_CACHE_SECONDS = 5 * 60

# Field-level encryption (SEC.3, docs/dev/field-encryption.md): keys live outside the database,
# in the file named by MPMV_FIELD_ENCRYPTION_KEYS_FILE (one per line) or in
# MPMV_FIELD_ENCRYPTION_KEYS (comma-separated). Each is "<id>:<base64url 32 bytes>", newest first:
# the first encrypts, the others only decrypt until `rotate_field_keys` has moved every value.
# System checks fail without a valid key (core.E301/E302) or when the stored data does not
# decrypt with these keys (core.E303/E304, run by `migrate`).
FIELD_ENCRYPTION_KEYS = env_secret_list("MPMV_FIELD_ENCRYPTION_KEYS")

# Feature flags and kill switches (ARC.8, docs/dev/feature-flags.md): this environment's defaults,
# e.g. MPMV_FEATURE_FLAGS="checkout=off"; staff switches in the admin override them. Each process
# caches the stored switches for FEATURE_FLAGS_CACHE_SECONDS, so a pulled kill switch takes effect
# everywhere within that time (at once in the process that saved it).
FEATURE_FLAGS = env_flags("MPMV_FEATURE_FLAGS")
FEATURE_FLAGS_CACHE_SECONDS = 5

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
# Powerful browser features are denied until a task needs one (e.g. payment for wallets).
PERMISSIONS_POLICY: dict[str, list[str]] = {
    "accelerometer": [],
    "camera": [],
    "geolocation": [],
    "gyroscope": [],
    "magnetometer": [],
    "microphone": [],
    "payment": [],
    "usb": [],
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
