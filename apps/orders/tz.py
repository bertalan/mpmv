"""The one place orders read the business time zone (D-31).

Numbering years, the fiscal retention date and the scrub dates all go through `business_tz()`,
which reads the operator configuration through `core.services.business_tz()` (ARC.27).

The database triggers of migrations 0006, 0008 and 0010 compute in `TRIGGER_TIME_ZONE`, frozen
in their SQL, so until ORD.41 rewrites them the business time zone must be that zone: otherwise
Python and the database would put the same order's tax retention end in different years. Orders
fail closed on any other zone: `trigger_zone_refusal` refuses it in the operator configuration,
and the orders.E101 check (`checks.py`) stops `migrate` and `check --database`.
"""

import datetime as dt
import zoneinfo

from django.utils.translation import gettext

TRIGGER_TIME_ZONE = "Europe/Rome"


def business_tz(at: dt.datetime | None = None) -> zoneinfo.ZoneInfo:
    """The business time zone of the operator version valid at `at` (default: now)."""
    from apps.core.services import business_tz as operator_tz

    return operator_tz(at)


def trigger_zone_refusal(name: str) -> str | None:
    """ARC.27 until ORD.41: refuse a business time zone the order triggers cannot follow."""
    if name == TRIGGER_TIME_ZONE:
        return None
    return gettext(
        "The order records still compute their retention dates in %(zone)s, so the business "
        "time zone must stay %(zone)s until a developer updates them (ORD.41)."
    ) % {"zone": TRIGGER_TIME_ZONE}
