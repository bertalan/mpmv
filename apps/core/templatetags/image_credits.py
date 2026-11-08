"""Image credit components (CMS.8, spec 10 §10.2), for every template that shows a MediaImage.

- `{% image_credit_of image as credit %}` gives the image's `Credit`, or None when its licence
  needs no attribution; `{% include "core/components/image_credit.html" %}` renders it inside
  the image's `<figcaption>`.
- `{% image_credits_disclosure credits %}` renders the "Image credits" disclosure at the end of
  a page for images shown without a caption area (heroes, banners, cards); nothing when the
  list is empty.
"""

from collections.abc import Sequence
from typing import Any

from django import template

from ..image_licences import Credit

register = template.Library()


@register.simple_tag
def image_credit_of(image: Any) -> Credit | None:
    credit: Credit | None = getattr(image, "credit", None)
    return credit


@register.inclusion_tag("core/components/image_credits_disclosure.html")
def image_credits_disclosure(credits: Sequence[Credit]) -> dict[str, Any]:
    return {"credits": list(credits)}
