from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.core import checks


def check_not_allowed_on_live(**kwargs: Any) -> list[checks.CheckMessage]:
    """seed.E001: the demo seed is never allowed where `LIVE_DATA` is set (ARC.10)."""
    if getattr(settings, "ALLOW_SEED", False) and getattr(settings, "LIVE_DATA", False):
        return [
            checks.Error(
                "The demo seed is allowed (MPMV_ALLOW_SEED) in settings that hold live data.",
                hint="Unset MPMV_ALLOW_SEED: the seed runs only in development, tests and STAGE.",
                id="seed.E001",
            )
        ]
    return []


class SeedConfig(AppConfig):
    """The demo seed (ARC.10). No models: it writes the other apps' rows through their services.

    It sits above every other app in the dependency graph (spec 01 §1.3), and no app imports it.
    """

    name = "apps.seed"
    label = "seed"
    audited_models: tuple[str, ...] = ()
    unaudited_models: dict[str, str] = {}

    def ready(self) -> None:
        checks.register(check_not_allowed_on_live)
