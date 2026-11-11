"""System checks of the fitment app (FIT.6): the anonymous garage cookie settings."""

from collections.abc import Iterable
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core import checks

from . import garage

STORAGE_MODES = ("exempt", "off")


def check_garage_cookie_settings(
    app_configs: Iterable[AppConfig] | None, **kwargs: Any
) -> list[checks.CheckMessage]:
    """The cookie is technical only while it lives at most 12 months (Q-G1); the mode is one of
    `exempt` and `off`."""
    problems: list[checks.CheckMessage] = []
    mode = getattr(settings, "GARAGE_ANON_STORAGE", "exempt")
    if mode not in STORAGE_MODES:
        problems.append(
            checks.Error(
                f"GARAGE_ANON_STORAGE is {mode!r}.",
                hint="Use 'exempt' (technical cookie, no consent gate) or 'off'.",
                id="fitment.E001",
            )
        )
    days = getattr(settings, "GARAGE_COOKIE_DAYS", garage.DEFAULT_DAYS)
    try:
        garage.cookie_max_age(days)
    except (TypeError, ValueError):
        problems.append(
            checks.Error(
                f"GARAGE_COOKIE_DAYS is {days!r}: the garage cookie lives at most 12 months.",
                hint=f"Set GARAGE_COOKIE_DAYS between 1 and {garage.MAX_DAYS}.",
                id="fitment.E002",
            )
        )
    return problems
