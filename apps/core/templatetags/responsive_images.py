"""`{% responsive_image image "<preset>" alt=… %}` (THM.12, spec 14 §14.7).

Renders a Wagtail image as a `<picture>` with AVIF and WebP sources and a JPEG fallback at the
preset's widths (`apps.core.images.PRESETS`: hero, content, gallery, card, logo), with `sizes`,
width and height. `priority=True` marks the LCP image (`fetchpriority="high"`, not lazy).
Nothing is rendered when `image` is empty.
"""

from typing import Any

from django import template
from django.utils.safestring import SafeString

from .. import images

register = template.Library()


@register.simple_tag
def responsive_image(image: Any, preset: str, **attrs: Any) -> SafeString | str:
    if not image:
        return ""
    return images.render_picture(image, preset, **attrs)
