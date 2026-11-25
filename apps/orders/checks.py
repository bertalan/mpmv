"""System checks of the orders app.

- orders.W101 (ARC.27, a database check): the business time zone differs from the zone the
  retention and scrub triggers compute in (`tz.TRIGGER_TIME_ZONE`, frozen in their SQL), so
  the Python dates and the database guards would disagree until a migration updates them.
"""

from collections.abc import Iterable
from typing import Any

from django.core import checks

from .tz import TRIGGER_TIME_ZONE, business_tz


def check_trigger_time_zone(
    app_configs: Iterable[Any] | None, databases: Iterable[str] | None = None, **kwargs: Any
) -> list[checks.CheckMessage]:
    if not databases:
        return []
    zone = business_tz().key
    if zone == TRIGGER_TIME_ZONE:
        return []
    return [
        checks.Warning(
            f"The business time zone is {zone}, but the order retention triggers compute in "
            f"{TRIGGER_TIME_ZONE}.",
            hint="Ship a migration that rewrites the triggers before changing the zone.",
            id="orders.W101",
        )
    ]
