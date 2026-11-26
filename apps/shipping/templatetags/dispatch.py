"""`{% closure_notice vendor_id %}`: the neutral closure notice of a vendor in a theme template
(VEN.20).

The storefront (VEN.10), the product page (CAT.7) and the cart read it through this library, so
`vendors`, `catalog` and `orders` gain no Python import of `shipping`. The text is a fixed
interface string with dates only, nothing the vendor writes. Every caller gets the same "today":
the date in the business time zone.
"""

from typing import Any

from django import template
from django.utils.formats import date_format

from apps.vendors.services import business_today

from .. import services

register = template.Library()


@register.inclusion_tag("shipping/closure_notice.html")
def closure_notice(vendor_id: int | None) -> dict[str, Any]:
    notice = services.closure_notice(vendor_id, business_today()) if vendor_id else None
    if notice is None:
        return {"notice": None}
    return {
        "notice": notice,
        "start": date_format(notice.start, "DATE_FORMAT"),
        "end": date_format(notice.end, "DATE_FORMAT"),
        "dispatch": date_format(notice.dispatch_on, "DATE_FORMAT"),
        "one_day": notice.start == notice.end,
    }
