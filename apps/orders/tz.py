"""The one place orders read the business time zone (D-31).

Numbering years, the fiscal retention date and the scrub dates all go through `business_tz()`.
When the project-wide helper arrives, only this function changes. The database triggers freeze
`Europe/Rome` (migrations 0006 and 0008); a change of zone needs a new migration too.
"""

import zoneinfo

from django.conf import settings


def business_tz() -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(settings.BUSINESS_TIME_ZONE)
