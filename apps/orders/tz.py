"""The one place orders read the business time zone (D-31).

Numbering years, the fiscal retention date and the scrub dates all go through `business_tz()`,
which reads the operator configuration through `core.services.business_tz()` (ARC.27).

The order guard trigger reads the same zone through `business_time_zone_at(ts)`, its database
twin (ORD.41, orders migration 0025), so Python and the database always put an order's tax
retention end and its scrub dates on the same days, whatever zone the operator chooses.
"""

import datetime as dt
import zoneinfo


def business_tz(at: dt.datetime | None = None) -> zoneinfo.ZoneInfo:
    """The business time zone of the operator version valid at `at` (default: now)."""
    from apps.core.services import business_tz as operator_tz

    return operator_tz(at)
