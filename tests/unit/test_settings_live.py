"""LIVE/STAGE settings serve the static files collected into the image (ARC.5)."""

import runpy
from typing import Any

from django.conf import settings


def live_settings() -> dict[str, Any]:
    return runpy.run_module("mpmv.settings.live")


def test_whitenoise_runs_right_after_the_security_middleware() -> None:
    middleware = live_settings()["MIDDLEWARE"]
    security = middleware.index("django.middleware.security.SecurityMiddleware")
    assert middleware[security + 1] == "whitenoise.middleware.WhiteNoiseMiddleware"


def test_static_files_use_hashed_compressed_storage() -> None:
    storages = live_settings()["STORAGES"]
    assert storages["staticfiles"]["BACKEND"] == (
        "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )
    assert "default" in storages


def test_other_environments_keep_the_base_middleware() -> None:
    live_settings()
    assert "whitenoise.middleware.WhiteNoiseMiddleware" not in settings.MIDDLEWARE
