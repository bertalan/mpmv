"""`{% responsive_image image "<preset>" alt=… %}` (THM.12, spec 14 §14.7).

Renders a Wagtail image as a `<picture>` with AVIF and WebP sources and a JPEG fallback at the
preset's widths (`apps.core.images.PRESETS`: hero, content, gallery, card, logo), with `sizes`,
width and height. Nothing is rendered when `image` is empty.

`priority=True` asks for the LCP treatment (`fetchpriority="high"`, not lazy). Only the first
such image of a response gets it: a page's lead image and every hero block ask for it, and
whichever comes first is the one above the fold. Later ones render lazily, so several heroes
never compete for bandwidth with the real LCP image. The claim is kept on the request, which
every include and StreamField block of the response shares; without a request (a template
rendered on its own) the image gets the priority it asks for.
"""

from typing import Any

from django import template
from django.utils.safestring import SafeString

from .. import images

register = template.Library()

_LCP_CLAIMED = "_mpmv_lcp_image_rendered"


def _claim_priority(context: template.Context) -> bool:
    """True for the first priority image of the response; False for every later one."""
    request = context.get("request")
    if request is None:
        return True
    if getattr(request, _LCP_CLAIMED, False):
        return False
    setattr(request, _LCP_CLAIMED, True)
    return True


@register.simple_tag(takes_context=True)
def responsive_image(
    context: template.Context, image: Any, preset: str, **attrs: Any
) -> SafeString | str:
    if not image:
        return ""
    if attrs.get("priority"):
        attrs["priority"] = _claim_priority(context)
    return images.render_picture(image, preset, **attrs)
