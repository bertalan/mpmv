"""System checks for the API settings, run by `manage.py check` (and so by `make check`).

- api.E003: the rate limit settings (`API_RATE_LIMIT`, `API_RATE_LIMIT_SCOPES`,
  `API_RATE_LIMIT_CLIENTS`, CORE.12) cannot be read; every API call would fail.
"""

from typing import Any

from django.core.checks import CheckMessage, Error, Tags, register

from .engine import InvalidRateLimit
from .services import rate_policy_from_settings


@register(Tags.security)
def check_rate_limit_settings(app_configs: Any = None, **kwargs: Any) -> list[CheckMessage]:
    try:
        rate_policy_from_settings()
    except InvalidRateLimit as exc:
        return [
            Error(
                f"Invalid API_RATE_LIMIT* setting: {exc}",
                hint="Rates read like 600/minute, scopes resource:action, client ids are UUIDs.",
                id="api.E003",
            )
        ]
    return []
