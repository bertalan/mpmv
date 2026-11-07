"""System checks for account security settings (CORE.5)."""

from collections.abc import Sequence
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error


def check_captcha_settings(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    """An enabled captcha without keys would lock everybody out of every login form."""
    if not settings.CAPTCHA_ENABLED:
        return []
    missing = [
        name
        for name in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY")
        if not getattr(settings, name)
    ]
    if not missing:
        return []
    return [
        Error(
            f"CAPTCHA_ENABLED is true but {' and '.join(missing)} is empty.",
            hint="Set the Turnstile keys in the environment, or set MPMV_CAPTCHA_ENABLED=false.",
            id="accounts.E001",
        )
    ]
