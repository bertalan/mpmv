"""Responsive image renditions (THM.12, spec 14 §14.7).

Templates render images through `{% responsive_image image "<preset>" alt=… %}`
(`apps.core.templatetags.responsive_images`): a `<picture>` with AVIF and WebP `<source>`
elements and a JPEG `<img>` fallback, each with a `srcset` of the preset's widths and its
`sizes`; the fallback carries width and height (no layout shift). An SVG logo keeps its format:
only resized, rendered as a plain `<img>` in the `<picture>`.

AVIF needs an image library that can encode it; the Pillow wheels in uv.lock bundle libavif,
so the container image can. Where it cannot, AVIF is left out (WebP and JPEG remain) and the
system check core.W501 says so.
"""

from dataclasses import dataclass
from functools import cache
from typing import Any, Literal

from django.utils.html import format_html
from django.utils.safestring import SafeString
from PIL import features

FORMATS = ("avif", "webp", "jpeg")  # the browser takes the first it supports; JPEG is the <img>


@dataclass(frozen=True)
class Preset:
    """Where an image sits: the rendition widths (CSS px at 1x up to 2x) and the `sizes`
    attribute that tells the browser how wide the slot is."""

    widths: tuple[int, ...]
    sizes: str
    op: Literal["width", "fill", "height"] = "width"
    ratio: tuple[int, int] | None = None  # for "fill": width to height
    fallback: str = "jpeg"  # the <img> format; PNG where transparency matters (logos)


PRESETS: dict[str, Preset] = {
    # Full-bleed opening banner: the LCP image of a landing page.
    "hero": Preset((640, 960, 1280, 1600, 1920), "100vw"),
    # An image in the content column (lead image, figure).
    "content": Preset((480, 800, 1200, 1600), "(min-width: 75rem) 72rem, 100vw"),
    # Gallery tiles: cropped 4:3.
    "gallery": Preset((480, 960), "(min-width: 48rem) 33vw, 100vw", op="fill", ratio=(4, 3)),
    # Product cards and tiles in a grid.
    "card": Preset((320, 480, 640), "(min-width: 64rem) 25vw, (min-width: 40rem) 50vw, 100vw"),
    # Logos in a strip: 3rem high, so 96 px covers 2x screens.
    "logo": Preset((96,), "6rem", op="height", fallback="png"),
}


@cache
def avif_supported() -> bool:
    """Pillow can encode AVIF here."""
    return bool(features.check("avif"))


@cache
def webp_supported() -> bool:
    return bool(features.check("webp"))


def picture_formats(*, avif: bool | None = None) -> tuple[str, ...]:
    """The `<picture>` formats in source order; AVIF only where it can be encoded."""
    if avif is None:
        avif = avif_supported()
    return FORMATS if avif else tuple(f for f in FORMATS if f != "avif")


def _operation(preset: Preset, width: int) -> str:
    if preset.op == "fill":
        rw, rh = preset.ratio or (1, 1)
        return f"fill-{width}x{width * rh // rw}"
    return f"{preset.op}-{width}"


def picture_specs(name: str, *, formats: tuple[str, ...], svg: bool = False) -> list[str]:
    """Wagtail filter specs for preset `name`: one per format and width; an SVG keeps its
    format, so it gets one per width only."""
    preset = PRESETS[name]
    operations = [_operation(preset, width) for width in preset.widths]
    if svg:
        return operations
    formats = tuple(preset.fallback if fmt == "jpeg" else fmt for fmt in formats)
    return [f"format-{fmt}|{operation}" for fmt in formats for operation in operations]


def render_picture(image: Any, name: str, **attrs: Any) -> SafeString:
    """`<picture>` HTML for a Wagtail image (see the module docstring).

    `priority=True` marks the LCP image: `fetchpriority="high"` and no lazy loading. Other
    attributes (`alt`, `class`) go on the `<img>`; `alt` defaults to "" (decorative).
    """
    from wagtail.images.models import Picture

    priority = bool(attrs.pop("priority", False))
    svg = bool(image.is_svg())
    specs = picture_specs(name, formats=picture_formats(), svg=svg)
    # Largest first: the <img> fallback takes the first rendition, and its width and height
    # (the intrinsic size the layout reserves) should be the largest, as `{% image %}` gave.
    renditions = image.get_renditions(*reversed(specs))
    img_attrs: dict[str, Any] = {"alt": attrs.pop("alt", "") or "", "sizes": PRESETS[name].sizes}
    if priority:
        img_attrs["fetchpriority"] = "high"
    else:
        img_attrs["loading"] = "lazy"
        img_attrs["decoding"] = "async"
    img_attrs.update({key: value for key, value in attrs.items() if value not in (None, "")})
    picture = Picture(renditions, img_attrs)
    return format_html("{}", picture)
