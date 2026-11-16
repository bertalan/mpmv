"""TAX.12: the displayed consumer price in templates (catalog, CMS, themes), which never import
the tax app.

    {% load consumer_prices %}
    {% consumer_price card.price card.fiscal_category card.vendor as price %}
    {% if price %}{{ currency }} {{ price.amount }} <small>{{ price.note }}</small>{% endif %}

The tag gives a `ConsumerPrice` (apps.tax.prices): the price checkout will charge for this
vendor and destination. None when there is no public net price (`on_request`, `catalog_only`:
never priced), no vendor, or no decision can be made; a template then shows no amount, never the
net price.
"""

import logging
from decimal import Decimal

from django import template
from django.http import HttpRequest

from apps.tax.prices import ConsumerPrice, PriceUnavailable, consumer_price
from apps.vendors.models import Vendor

register = template.Library()
logger = logging.getLogger(__name__)


@register.simple_tag(takes_context=True, name="consumer_price")
def consumer_price_tag(
    context: template.Context, net: Decimal | None, fiscal_category: str, vendor: Vendor | None
) -> ConsumerPrice | None:
    if net is None or not isinstance(vendor, Vendor):
        return None
    request = context.get("request")
    try:
        return consumer_price(
            net,
            fiscal_category,
            vendor=vendor,
            request=request if isinstance(request, HttpRequest) else None,
        )
    except PriceUnavailable as exc:
        logger.warning("consumer price unavailable (%s): %s", fiscal_category, exc)
        return None
