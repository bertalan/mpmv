"""Template tags of the compliance app (CMP.3).

`{% product_safety product %}` renders the GPSR block of a product page (GPSR art. 19): the
product page of CAT.7 includes it next to the product picture.

`{% product_destination_notice product country %}` explains, on the product page and at checkout
(ORD.5), why a product cannot be shipped to `country` (its warnings are not in a language of that
country, owner decision 2026-11-09) and links to the warnings in the languages that exist. It
renders nothing when the sale is allowed.
"""

from typing import Any

from django import template
from django.conf import settings
from django.utils import translation

from apps.catalog.models import Product
from apps.compliance import services
from apps.core.i18n import translate_path

WARNINGS_ANCHOR = "#product-safety-texts"

register = template.Library()


@register.inclusion_tag("compliance/includes/product_safety.html")
def product_safety(product: Product) -> dict[str, Any]:
    """The product's GPSR data in the active language (warnings fall back to the primary one)."""
    language = translation.get_language() or ""
    return {"gpsr": services.gpsr_display(product, language), "language": language}


@register.inclusion_tag("compliance/includes/destination_notice.html", takes_context=True)
def product_destination_notice(
    context: template.Context, product: Product, country: str
) -> dict[str, Any]:
    """The notice for a destination `product` cannot be shipped to; empty when it can."""
    check = services.warnings_sale_check(product, country)
    if check.allowed:
        return {"check": check, "links": []}
    request = context.get("request")
    path = request.path if request is not None else ""
    names = dict(settings.LANGUAGES)
    links = [
        {
            "language": code,
            "name": names.get(code, code),
            "href": (translate_path(path, code) if path else "") + WARNINGS_ANCHOR,
        }
        for code in check.available_languages
    ]
    return {"check": check, "links": links}
