"""`{% seller_name vendor as name %}`: the public seller name of a vendor in a template (SEO.15).

The catalog never imports the seo app (spec 01 graph), so its templates ask for the one rule
(`engine.public_seller_name`, shared with the structured data) through this tag. None when the
vendor has no name that may be shown (a natural person's legal name): the template then shows
no name rather than the private one.
"""

from django import template

from apps.seo import seller
from apps.vendors.models import Vendor

register = template.Library()


@register.simple_tag(name="seller_name")
def seller_name_tag(vendor: object) -> str | None:
    return seller.public_name(vendor) if isinstance(vendor, Vendor) else None
