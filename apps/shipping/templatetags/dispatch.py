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
from ..engine import ClosureNotice

register = template.Library()


def _context(notice: ClosureNotice | None, vendor_id: int) -> dict[str, Any]:
    if notice is None:
        return {"notice": None}
    return {
        "notice": notice,
        "heading_id": f"closure-notice-{vendor_id}",
        "start": date_format(notice.start, "DATE_FORMAT"),
        "end": date_format(notice.end, "DATE_FORMAT"),
        "dispatch": date_format(notice.dispatch_on, "DATE_FORMAT"),
        "one_day": notice.start == notice.end,
    }


@register.inclusion_tag("shipping/closure_notice.html")
def closure_notice(vendor_id: int | None) -> dict[str, Any]:
    """The notice of one vendor (one query): storefront and product page."""
    if not vendor_id:
        return {"notice": None}
    return _context(services.closure_notice(vendor_id, business_today()), vendor_id)


@register.inclusion_tag("shipping/closure_notice.html")
def show_closure_notice(notice: ClosureNotice | None, vendor_id: int) -> dict[str, Any]:
    """A notice already read for many vendors at once (`services.closure_notices`): the cart."""
    return _context(notice, vendor_id)
