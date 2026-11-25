"""The one place orders read the business time zone (D-31).

Numbering years, the fiscal retention date and the scrub dates all go through `business_tz()`,
which reads the operator configuration through `core.services.business_tz()` (ARC.27).

The database triggers of migrations 0006, 0008 and 0010 compute in `TRIGGER_TIME_ZONE`, frozen
in their SQL. A different business time zone therefore needs a new migration for the triggers;
until then the orders.W101 check warns (see `checks.py`).
"""

import zoneinfo

TRIGGER_TIME_ZONE = "Europe/Rome"


def business_tz() -> zoneinfo.ZoneInfo:
    from apps.core.services import business_tz as operator_tz

    return operator_tz()
