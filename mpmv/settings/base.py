"""Settings shared by every environment."""

from pathlib import Path

import dj_database_url
import django_stubs_ext
from django.utils.csp import CSP

from .env import (
    env_bool,
    env_flags,
    env_float,
    env_int,
    env_list,
    env_secret_list,
    env_str,
    map_tile_origin,
)

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
    "apps.events",  # event pages (EVT.1, spec 15): after cms, whose locales its indexes need
    "apps.themes",  # frontend themes (THM.1, spec 14): imports only core and Wagtail
    "apps.seo",
    "apps.compliance",
    "apps.api",
    "apps.mcp",
    "apps.manager",  # the Manager backoffice (CORE.17): an adapter like api, no models
    "apps.seed",  # demo data seed (ARC.10): above every app, imported by none
]

# Demo data seed (ARC.10, docs/dev/demo-seed.md): off unless allowed. Dev and test allow it;
# STAGE opts in with MPMV_ALLOW_SEED=1. LIVE_DATA marks an environment holding real data
# (live.py): the seed never runs there, and the `seed.E001` check fails if it is allowed.
ALLOW_SEED = env_bool("MPMV_ALLOW_SEED", default=False)
# Public STAGE demo (ARC.22) and the search engine floor (SEO.8, D-35): either one makes every
# response noindex (meta, X-Robots-Tag and robots.txt). STAGE sets FORCE_NOINDEX itself.
DEMO_MODE = env_bool("MPMV_DEMO_MODE", default=False)
FORCE_NOINDEX = env_bool("MPMV_FORCE_NOINDEX", default=False)
LIVE_DATA = False

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
    "wagtail.contrib.routable_page",  # `routablepageurl` for the blog's author pages (CMS.3)
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
    # Outermost, so it wraps SecurityMiddleware's own HTTPS 301 too (SEC.5 triage, SEC.12).
    "apps.core.middleware.CrossOriginResourcePolicyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "apps.cms.middleware.WagtailAdminPickerNonceMiddleware",  # Wagtail's inline pickers (CMS.1)
    "apps.core.middleware.PermissionsPolicyMiddleware",
    "apps.seo.middleware.ForceNoindexMiddleware",  # X-Robots-Tag when forced (SEO.8)
    # With the other header middleware, so responses returned early by an inner middleware
    # (CommonMiddleware's APPEND_SLASH redirect, LIVE's static files) carry it too (SEC.5).
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # SEC.13/SEC.14: Wagtail's page view restriction forms answer 404 until they are protected.
    "apps.cms.middleware.PageRestrictionBlockMiddleware",
    "apps.themes.middleware.ThemeMiddleware",  # the site's theme for the template loader (THM.1)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    # CAT.6: legacy redirect rules on a 404 (after CAT.5) and GET ?add-to-cart= to the product.
    "apps.catalog.middleware.LegacyRedirectMiddleware",
    # CAT.5: a 404 on a product's old slug becomes a 301 to its current slug.
    "apps.catalog.middleware.OldProductSlugRedirectMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # I18N.12: the stored language of a signed-in user on unprefixed areas (Manager, account).
    "apps.accounts.middleware.PreferredLanguageMiddleware",
    # SEC.5: pages for a signed-in user are never stored by a shared cache.
    "apps.core.middleware.PrivateCacheForSignedInUsersMiddleware",
    # CORE.4: which device verified the session, then confine required roles to the MFA pages.
    "django_otp.middleware.OTPMiddleware",
    "apps.accounts.middleware.MfaRequiredMiddleware",
    # CORE.17: one gate per Manager space (404, login or MFA) for every URL under /manager/.
    "apps.manager.middleware.ManagerGateMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # EVT.3: wagtail-localize's views keep the event moderation rules (hidden, review).
    "apps.events.middleware.LocalizeEventGuardMiddleware",
]

# CAT.5/CAT.6: how long a browser or proxy keeps a 301 (or a 410) of a redirect rule, so a rule
# staff change, delete or let expire stops applying within a day; capped at the rule's expiry.
REDIRECT_PERMANENT_MAX_AGE = 24 * 60 * 60

# Custom user from the first migration (CORE.1): email login, preferred language.
AUTH_USER_MODEL = "accounts.User"
# Lockout and captcha checks run first, before any password is checked (CORE.5).
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.LoginProtectionBackend",
    # Vendors do not get the Wagtail admin (spec 15): before ModelBackend, whose groups grant it.
    "apps.vendors.backends.VendorAdminRefusalBackend",
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
# The Manager login (CORE.17): where the Manager gate sends anonymous visitors.
LOGIN_URL = "login"
MFA_LOGIN_URL = "admin:login"  # until the site and vendor login pages exist
# Secure action links in emails (CORE.15, spec 02): validity of a link unless its purpose sets
# its own, and Continue POSTs allowed per client IP every 15 minutes.
ACTION_LINK_MAX_AGE = 72 * 3600  # seconds
ACTION_LINK_RATE_LIMIT = 30
# Vendor claim (VEN.24): validity of a claim link, wrong VAT numbers or TINs before the claim
# locks (staff then regenerate the link), and claim-page POSTs allowed per IP every 15 minutes.
VENDOR_CLAIM_LINK_MAX_AGE = 72 * 3600  # seconds
VENDOR_CLAIM_MAX_FAILURES = 5
VENDOR_CLAIM_RATE_LIMIT = 20
# Storefront profile saves per vendor owner per hour (CORE.9): each one emails the moderators.
VENDOR_PROFILE_SAVE_LIMIT = 20
VENDOR_PROFILE_VENDOR_SAVE_LIMIT = 30  # all owners of one vendor together
# Public "Sell with us" application (VEN.22): validity of the email confirmation link, and
# confirm-page POSTs allowed per IP every 15 minutes (the form uses the registration limit).
VENDOR_APPLICATION_LINK_MAX_AGE = 72 * 3600  # seconds
VENDOR_APPLICATION_RATE_LIMIT = 20
# Unconfirmed applications (owner decision D-24, VEN.22): the account and its applicant vendor
# are deleted this many days after the request, and one reminder goes out after this many hours.
VENDOR_APPLICATION_RETENTION_DAYS = 3
VENDOR_APPLICATION_REMINDER_HOURS = 48
MFA_ISSUER = "MPMV"  # name shown in authenticator apps and passkey prompts
OTP_TOTP_ISSUER = MFA_ISSUER
OTP_ADMIN_HIDE_SENSITIVE_DATA = True

# Vendor deletion (CORE.14): `vendors` cannot import the apps above it (spec 01 §1.3). Each dotted
# path names a `hook(vendor, changes)` that adds one delete to the deletion's ChangeSet per row
# the deletion cascades to, so every such row gets its own AuditLog entry.
VENDOR_DELETION_AUDIT_HOOKS: list[str] = [
    "apps.api.services.vendor_key_deletions",  # the vendor's API keys (CORE.13)
]
# Vendor closure (CORE.14, VEN.21): each dotted path names a `hook(vendor, *, ctx, dry_run) ->
# ChangeSet` that revokes, audited, what only an active vendor may hold. Run when a vendor is
# closed, and again when it is reopened (rows a closure left behind); its changes join the
# closure's or reopen's ChangeSet.
VENDOR_CLOSURE_HOOKS: list[str] = [
    "apps.api.services.revoke_vendor_keys",  # the vendor's API keys
]

# Allowed vendor countries (CMP.13): `core` cannot import `vendors`, so this names the function
# that lists the active vendors established outside a new allowed set.
VENDORS_OUTSIDE_COUNTRIES_HOOK = "apps.vendors.services.active_vendors_outside"

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
            "builtins": ["apps.seo.templatetags.seo_robots"],
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
# Built theme CSS (THM.2): `make assets` writes themes/<slug>/{tokens,theme}.css here
# (git-ignored); ThemeAssetsFinder serves it to runserver and collectstatic.
MPMV_THEME_BUILD_DIR = BASE_DIR / "build" / "themes"
# Help/contact link shown in the same place in the header and footer of every public page
# (THM.3, WCAG 3.2.6 consistent help); empty = no help link until a contact page exists.
MPMV_HELP_URL = env_str("MPMV_HELP_URL", "")

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

# Transactional email (ARC.16, docs/dev/transactional-email.md). Every email goes through the
# outbox (`apps.core.services.enqueue_email`) and the `EmailSender` named here: the console
# backend until ARC.19 adds the EU provider's adapter (provider choice pending, Q-48). The
# provider webhook (`/webhooks/email/`) is off while MPMV_EMAIL_WEBHOOK_SECRET is empty. Empty
# variables mean the defaults below; settings holding live data refuse the console sender and a
# localhost address (system checks core.E501, core.E502).
EMAIL_SENDER = env_str("MPMV_EMAIL_SENDER", "") or "apps.core.mail.ConsoleEmailSender"
DEFAULT_FROM_EMAIL = env_str("MPMV_EMAIL_FROM", "") or "no-reply@localhost"
EMAIL_WEBHOOK_SECRET = env_str("MPMV_EMAIL_WEBHOOK_SECRET", "")
EMAIL_FEEDBACK_PARSER = (
    env_str("MPMV_EMAIL_FEEDBACK_PARSER", "") or "apps.core.mail.SignedJsonFeedback"
)

# Wagtail (CMS.1, spec 10). Page locales follow LANGUAGES: apps.cms creates a Locale row for each
# language after every migrate. The editors' admin is at /admin/ (mpmv/urls.py).
WAGTAIL_SITE_NAME = "MPMV"
WAGTAIL_I18N_ENABLED = True
# Approval publishes the reviewed revision, not a later draft (EVT.3): apps/core/workflows.py.
WAGTAIL_FINISH_WORKFLOW_ACTION = "apps.core.workflows.publish_reviewed_revision"
WAGTAIL_CONTENT_LANGUAGES = LANGUAGES
# The public address of the site, in Wagtail's admin emails and previews. The localhost fallback
# is for a developer machine: `check --deploy` fails on LIVE and STAGE without it (cms.E001).
SITE_BASE_URL = env_str("MPMV_BASE_URL", "") or "http://localhost:8000"
WAGTAILADMIN_BASE_URL = SITE_BASE_URL
# One image model for CMS pages, products (CAT.4), vendor logos and banners (spec 10 §10.2). Set
# before any image exists: the licence and credit fields come with CMS.8.
WAGTAILIMAGES_IMAGE_MODEL = "core.MediaImage"
# Upload limits (SEC.2, apps.core.uploads; values decided on PR #164). Wagtail's form checks the
# size and the pixels; MediaImage checks all of them on every path and re-encodes the stored
# original. A still image decodes at most 60 megapixels (45-60 MP camera originals); above it,
# it is refused. An animation (GIF, WebP, APNG: logos and banners, not photos) decodes at most 16
# megapixels, all its frames together. Both are checked on the headers, before any frame is
# decoded. An original over 40 megapixels is downscaled (same aspect ratio) when re-encoded, so
# stored originals and their renditions stay within 40. 300 frames bound the CPU an animation of
# tiny frames costs (each frame is decoded and re-encoded).
# Memory: Pillow holds 4 bytes a pixel. Measured on PR #164: a 100-megapixel still image peaked
# at about 600 MB of RSS while re-encoded (the decoded original, 400 MB, plus the stored
# 40-megapixel copy, 160 MB), an animated WebP of 100 megapixels at about 1 GB (libwebp keeps
# two full canvases). At these caps that is about 450 MB for a 60-megapixel still image (240 MB
# decoded plus the 160 MB stored copy) and about 160 MB for a 16-megapixel animation: about
# 450 MB per worker process for uploads, WEB_CONCURRENCY times that per host. Production runs
# gunicorn sync workers (one request per process), so the per-process decode lock
# (apps.core.uploads.decode_slot) is never contended there: it guards threaded or async workers
# and management commands, where a second upload waits MPMV_IMAGE_DECODE_WAIT_SECONDS (at most
# 300), then gets a "try again" error, never a server error. Wagtail renditions (up to the
# 40-megapixel stored original, about 160 MB) decode outside the lock.
# CoreConfig.ready sets Pillow's bomb threshold to the still-image cap. Every cap can be set
# from the environment (deploy/server/mpmv.env.example); core.E601/E602 check them at startup,
# and a value that is not a number stops the settings import (ImproperlyConfigured).
# Raster formats are checked and re-encoded (apps.core.uploads); SVG is for logos and icons only,
# in an SvgCollection, and goes through apps.core.svg instead (THM.12). The branch is chosen by
# content, never by the extension, and the extension must agree with the content.
WAGTAILIMAGES_EXTENSIONS = ["avif", "gif", "jpg", "jpeg", "png", "webp", "svg"]
WAGTAILIMAGES_MAX_UPLOAD_SIZE = env_int("MPMV_MAX_IMAGE_UPLOAD_BYTES", 25 * 1024 * 1024)
WAGTAILIMAGES_MAX_IMAGE_PIXELS = env_int("MPMV_MAX_IMAGE_PIXELS", 60_000_000)
MPMV_MAX_ANIMATION_PIXELS = env_int("MPMV_MAX_ANIMATION_PIXELS", 16_000_000)
MPMV_MAX_IMAGE_SIDE = env_int("MPMV_MAX_IMAGE_SIDE", 16_000)
MPMV_MAX_STORED_IMAGE_PIXELS = env_int("MPMV_MAX_STORED_IMAGE_PIXELS", 40_000_000)
MPMV_MAX_IMAGE_FRAMES = env_int("MPMV_MAX_IMAGE_FRAMES", 300)
MPMV_IMAGE_DECODE_WAIT_SECONDS = env_float("MPMV_IMAGE_DECODE_WAIT_SECONDS", 30.0)
# Safe parsing of the upload field (SEC.2) and the SVG refusal in the second step of Wagtail's
# multiple upload (THM.12).
WAGTAILIMAGES_IMAGE_FORM_BASE = "apps.core.forms.MediaImageForm"
# Wagtail documents exist for wagtail-localize only (I18N.3): PDF only, checked by content.
WAGTAILDOCS_EXTENSIONS = ["pdf"]
WAGTAILDOCS_MAX_UPLOAD_SIZE = 10 * 1024 * 1024
WAGTAILDOCS_DOCUMENT_FORM_BASE = "apps.core.document_forms.DocumentForm"
# No document serve view is mounted: a document's URL is its media path, which Caddy answers
# with 404 (deploy/caddy/Caddyfile). Without this, the admin's document pages fail on the
# missing `wagtaildocs_serve` route.
WAGTAILDOCS_SERVE_METHOD = "direct"
# Renditions without an explicit format-* go to WebP. GIF is left out: Wagtail turns a still GIF
# into PNG and keeps an animated one as GIF, which WebP conversion would flatten to one frame.
# Templates ask for AVIF, WebP and JPEG explicitly ({% responsive_image %}, apps.core.images).
WAGTAILIMAGES_FORMAT_CONVERSIONS = {
    "png": "webp",
    "jpeg": "webp",
    "bmp": "webp",
    "heic": "webp",
    "webp": "webp",
    "avif": "avif",
}
# wagtail-localize (I18N.3): translations follow their source page; a translator syncs them from
# the source page's "Sync translated pages" action.
WAGTAIL_LOCALIZE_DEFAULT_TRANSLATION_MODE = "synced"
WAGTAILSEARCH_BACKENDS = {"default": {"BACKEND": "wagtail.search.backends.database"}}
# Passwords, email addresses, users and groups are managed where changes are audited (SEC.4),
# not from Wagtail's account and user pages.
WAGTAIL_PASSWORD_MANAGEMENT_ENABLED = False
# Editors' login with autocomplete tokens and the show-password toggle (CMP.9, WCAG 2.2 SC 3.3.8);
# its template (cms/admin/login.html, routed in apps.cms.admin_urls) renders the captcha.
WAGTAILADMIN_USER_LOGIN_FORM = "apps.cms.admin_forms.EditorLoginForm"
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
# Public holidays (ISO dates) the GPSR article 22 deadlines skip (CMP.3). Empty: working days are
# Monday to Friday, the earliest deadline (owner decision 2026-11-09).
COMPLIANCE_PUBLIC_HOLIDAYS: list[str] = []
# The country -> accepted warnings languages map (CMP.3) is cached per process this long; a staff
# edit drops this process's copy at once, other processes reload within this time.
COMPLIANCE_COUNTRY_LANGUAGES_CACHE_SECONDS = 300

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "apps.themes.finders.ThemeAssetsFinder",  # built theme CSS (THM.2)
]
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
VIES_API_URL = (
    env_str("MPMV_VIES_API_URL", "")
    or "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
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

# Server-side geocoding (EVT.12, spec 15): the provider chain in order, each name mapped to its
# adapter. Adding a provider is a new adapter and an entry here. Keyed providers are skipped
# without a key; keys come from the environment only. Nominatim's usage policy: at most one
# request per second for the whole platform (the PostgreSQL limiter) and an identifying
# User-Agent with the operator contact.
GEOCODING_PROVIDERS = env_list("MPMV_GEOCODING_PROVIDERS", "nominatim,photon,locationiq,opencage")
GEOCODING_ADAPTERS = {
    "nominatim": "apps.core.geocoding.adapters.NominatimGeocoder",
    "photon": "apps.core.geocoding.adapters.PhotonGeocoder",
    "locationiq": "apps.core.geocoding.adapters.LocationIQGeocoder",
    "opencage": "apps.core.geocoding.adapters.OpenCageGeocoder",
}
GEOCODING_TRANSPORT = "apps.core.geocoding.adapters.http_get_json"
GEOCODING_CONTACT = env_str("MPMV_GEOCODING_CONTACT", "")
GEOCODING_LOCATIONIQ_KEY = env_str("MPMV_GEOCODING_LOCATIONIQ_KEY", "")
GEOCODING_OPENCAGE_KEY = env_str("MPMV_GEOCODING_OPENCAGE_KEY", "")
GEOCODING_MIN_INTERVALS = {"nominatim": 1.0, "photon": 1.0, "locationiq": 0.5, "opencage": 1.0}
GEOCODING_CALL_TIMEOUT = 3.0  # seconds per provider call
GEOCODING_CHAIN_BUDGET = 10.0  # seconds for the whole chain
GEOCODING_BREAKER_THRESHOLD = 5  # consecutive failures or rate limits that open the circuit
GEOCODING_BREAKER_COOLDOWN = 300  # seconds before an open circuit lets one try through
GEOCODING_CACHE_DAYS = 180
GEOCODING_USER_LIMIT = 10  # "Find coordinates" lookups per user and window
GEOCODING_USER_WINDOW = 60  # seconds
# Who may use "Find coordinates" besides staff: dotted `check(user) -> bool` paths, so that core
# does not import the vendors app.
GEOCODING_ACCESS_POLICIES: list[str] = ["apps.vendors.services.is_vendor_member"]

# Maps (EVT.13, spec 15 "Maps"): the Leaflet tile URL template, its attribution and the view of
# a map without a point (latitude, longitude, zoom). The tiles come from the EVT.14 proxy on the
# site itself (`/maps/tiles/{z}/{x}/{y}.png`) once it exists; until then empty means no tile
# layer (the picker still works: click, drag and the inputs). An absolute https URL is allowed in
# the CSP for that one origin only, and never in tests (no third-party network).
MAP_TILE_URL = env_str("MPMV_MAP_TILE_URL", "")
MAP_TILE_ATTRIBUTION = env_str("MPMV_MAP_TILE_ATTRIBUTION", "") or "© OpenStreetMap contributors"
MAP_TILE_ATTRIBUTION_URL = (
    env_str("MPMV_MAP_TILE_ATTRIBUTION_URL", "") or "https://www.openstreetmap.org/copyright"
)
MAP_DEFAULT_VIEW = (48.0, 10.0, 4)
_MAP_TILE_ORIGIN = map_tile_origin(MAP_TILE_URL)
if _MAP_TILE_ORIGIN:
    SECURE_CSP = {
        **SECURE_CSP,
        "img-src": [*SECURE_CSP["img-src"], _MAP_TILE_ORIGIN],  # type: ignore[misc]
    }

# Per-API-key rate limit (CORE.12, CORE.16, apps.api.engine): fixed windows counted in PostgreSQL,
# so every app process shares one counter per key. API_RATE_LIMIT is the key-wide limit of every
# key, set per environment with MPMV_API_RATE_LIMIT (default 120/minute, owner decision MQ-4);
# API_RATE_LIMIT_SCOPES gives a costly scope its own counter and limit for every key, e.g.
# {"catalog:write": "60/minute"}; API_RATE_LIMIT_CLIENTS replaces the key-wide limit of one key,
# by ApiClient public id. A per-key override that staff set in the admin (ApiClient.rate_limit)
# wins over both. Rates read "<calls>/<second|minute|hour|day>" (system check api.E003).
API_RATE_LIMIT = env_str("MPMV_API_RATE_LIMIT", "") or "120/minute"
API_RATE_LIMIT_SCOPES: dict[str, str] = {}
API_RATE_LIMIT_CLIENTS: dict[str, str] = {}
# Refused API keys (401) per client IP (CORE.20, apps.api.services.record_refused_key): the IP
# (resolved behind TRUSTED_PROXIES, IPv6 by /64) may get API_REFUSED_KEY_RATE_LIMIT refused
# answers in a rolling window; the one that reaches it blocks the IP for
# API_REFUSED_KEY_BLOCK_SECONDS, answered 429 with Retry-After before any key lookup. Valid keys
# never count. Set per environment with MPMV_API_REFUSED_KEY_RATE_LIMIT and
# MPMV_API_REFUSED_KEY_BLOCK_SECONDS (system check api.E004).
# Trade-off: the block covers the whole IP (or /64), valid keys included, so one client behind a
# NAT shared with other API clients can keep them out; and if TRUSTED_PROXY_HEADER or
# TRUSTED_PROXIES is wrong in production every client resolves to the proxy address, and one bad
# client blocks the API for everyone. Check the proxy settings before going live.
API_REFUSED_KEY_RATE_LIMIT = env_str("MPMV_API_REFUSED_KEY_RATE_LIMIT", "") or "30/minute"
API_REFUSED_KEY_BLOCK_SECONDS = int(env_str("MPMV_API_REFUSED_KEY_BLOCK_SECONDS", "") or "300")

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
# Everything is same-origin: no other site embeds our resources (apps.core.middleware).
CROSS_ORIGIN_RESOURCE_POLICY = "same-origin"
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

# A secure action link holds its token in the URL path (CORE.15): request logs redact it. The
# filter on `django.request` runs before propagation, so Django's own handlers get it too.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_secret_paths": {"()": "mpmv.log_redaction.RedactSecretPaths"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "filters": ["redact_secret_paths"]},
    },
    "loggers": {"django.request": {"filters": ["redact_secret_paths"]}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# SEC.13 (apps/core/public_forms.py): URL prefixes the anonymous-POST sweep does not walk, each
# with the reason an anonymous request is refused there. A login form under one of them (the
# admins' sign-in) is declared on its own view and listed in docs/dev/public-forms.md.
PUBLIC_FORM_GATED_PREFIXES = {
    "manager/": "the Manager gate answers 404 to anyone without a space (manager.E001, E002)",
    "admin/": "the editors' admin requires a signed-in editor; its sign-in form is declared",
    "django-admin/": "the staff admin requires a signed-in staff user; its sign-in is declared",
    "api/": "API v1 authenticates every operation (API key or session)",
    "_styleguide/": "staff only (THM.4)",
    "_util/": "Wagtail page view restriction forms answer 404 while BLOCK_PAGE_RESTRICTION_ROUTES "
    "is on and no restriction exists (cms.E002); SEC.14 protects them",
}

# SEC.13/SEC.14: Wagtail's page view restriction forms (`/_util/...`) have no rate limit or captcha
# yet, so apps.cms.middleware.PageRestrictionBlockMiddleware answers 404 to them and `check` fails
# (cms.E002) while any password or login restriction exists. SEC.14 lifts the block.
BLOCK_PAGE_RESTRICTION_ROUTES = True

# Optional prefix of order numbers, e.g. "MP-" gives "MP-2027-000123" (ORD.2). Empty by default.
ORDER_NUMBER_PREFIX = ""

# Months after an order during which its non-fiscal data is kept; then `minimise_order` may
# drop it (D-29). The fiscal snapshot is kept 10 years from the end of the order's year.
ORDER_ACTIVE_PHASE_MONTHS = 24  # can only lengthen the 24-month floor enforced in the database
