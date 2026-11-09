"""LIVE/STAGE settings serve the static files collected into the image (ARC.5)."""

import runpy
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string
from whitenoise.storage import CompressedManifestStaticFilesStorage

from apps.cms.staticfiles import StaticFilesStorage


def live_settings() -> dict[str, Any]:
    return runpy.run_module("mpmv.settings.live")


# The middleware that only add security headers (SEC.1). WhiteNoise answers static files itself,
# so it runs inside all of them and static responses carry the headers too (SEC.5).
HEADER_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "apps.cms.middleware.WagtailAdminPickerNonceMiddleware",
    "apps.core.middleware.PermissionsPolicyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


def test_whitenoise_runs_right_after_the_security_header_middleware() -> None:
    middleware = live_settings()["MIDDLEWARE"]
    whitenoise = middleware.index("whitenoise.middleware.WhiteNoiseMiddleware")
    assert middleware[:whitenoise] == HEADER_MIDDLEWARE


def test_static_files_use_hashed_compressed_storage() -> None:
    storages = live_settings()["STORAGES"]
    backend = import_string(storages["staticfiles"]["BACKEND"])
    # WhiteNoise's storage, tolerant of Wagtail's references to apps left out (CMS.1).
    assert backend is StaticFilesStorage
    assert issubclass(backend, CompressedManifestStaticFilesStorage)
    assert "default" in storages


def test_other_environments_keep_the_base_middleware() -> None:
    live_settings()
    assert "whitenoise.middleware.WhiteNoiseMiddleware" not in settings.MIDDLEWARE
