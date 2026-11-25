"""System checks of the orders app.

- orders.E101 (ARC.27, a database check, so it runs in `migrate` and `check --database`): the
  business time zone differs from the zone the retention and scrub triggers compute in
  (`tz.TRIGGER_TIME_ZONE`, frozen in their SQL). Python and the database guards would then put
  an order's tax retention end in different years, so the release stops until ORD.41 makes the
  triggers read the operator's zone. An unknown configured zone is left to core.E931.
"""

from collections.abc import Iterable
from typing import Any

from django.core import checks
from django.core.exceptions import ImproperlyConfigured

from .tz import TRIGGER_TIME_ZONE, business_tz


def check_trigger_time_zone(
    app_configs: Iterable[Any] | None, databases: Iterable[str] | None = None, **kwargs: Any
) -> list[checks.CheckMessage]:
    if not databases:
        return []
    try:
        zone = business_tz().key
    except ImproperlyConfigured:
        return []  # core.E931 reports the unknown MPMV_BUSINESS_TIME_ZONE
    if zone == TRIGGER_TIME_ZONE:
        return []
    return [
        checks.Error(
            f"The business time zone is {zone}, but the order retention and scrub triggers "
            f"compute in {TRIGGER_TIME_ZONE}: the tax retention dates would split between the "
            "application and the database.",
            hint=f"Set MPMV_BUSINESS_TIME_ZONE={TRIGGER_TIME_ZONE} (and the operator's business "
            "time zone) until ORD.41 makes the triggers read the operator's zone.",
            id="orders.E101",
        )
    ]
