"""Template tags of the compliance app (CMP.3).

`{% product_safety product country %}` renders the GPSR block of a product page (GPSR art. 19):
the product page of CAT.7 includes it next to the product picture. The warnings always show
when they exist, in another language (labelled) when none matches the page.

`{% product_destination_notice product country product_url %}` explains, on the product page and
at checkout (ORD.5), why a product cannot be shipped to `country` (its warnings are not in a
language of that country, owner decision 2026-11-09) and links to the warnings in the languages
that exist, on the product page `product_url` (default: the current page). It renders nothing
when the sale is allowed.
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
def product_safety(product: Product, country: str = "") -> dict[str, Any]:
    """The product's GPSR data in the active language; the warnings fall back to a language of
    the destination `country`, then the primary one, then any (`services.display_language`)."""
    language = translation.get_language() or ""
    return {"gpsr": services.gpsr_display(product, language, country), "language": language}


@register.inclusion_tag("compliance/includes/destination_notice.html", takes_context=True)
def product_destination_notice(
    context: template.Context, product: Product, country: str, product_url: str = ""
) -> dict[str, Any]:
    """The notice for a destination `product` cannot be shipped to; empty when it can. At
    checkout pass `product_url`: the links must reach the product page's warnings."""
    check = services.warnings_sale_check(product, country)
    if check.allowed:
        return {"check": check, "links": []}
    request = context.get("request")
    path = product_url or (request.path if request is not None else "")
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
