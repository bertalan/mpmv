"""Template tags of the compliance app (CMP.3).

`{% product_safety product %}` renders the GPSR block of a product page (GPSR art. 19): the
product page of CAT.7 includes it next to the product picture.
"""

from typing import Any

from django import template
from django.utils import translation

from apps.catalog.models import Product
from apps.compliance import services

register = template.Library()


@register.inclusion_tag("compliance/includes/product_safety.html")
def product_safety(product: Product) -> dict[str, Any]:
    """The product's GPSR data in the active language (warnings fall back to the primary one)."""
    language = translation.get_language() or ""
    return {"gpsr": services.gpsr_display(product, language), "language": language}
