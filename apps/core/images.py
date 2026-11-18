"""Responsive image renditions (THM.12, spec 14 §14.7).

Templates render images through `{% responsive_image image "<preset>" alt=… %}`
(`apps.core.templatetags.responsive_images`): a `<picture>` with AVIF and WebP `<source>`
elements and a JPEG `<img>` fallback, each with a `srcset` of the preset's widths and its
`sizes`; the fallback carries width and height (no layout shift). An SVG logo keeps its format:
only resized, rendered as a plain `<img>` in the `<picture>`.

A page never builds a rendition (CAT.4, CAT.24): it uses the ones that exist (`prefetch_renditions`
loads them for a list of images in one query) and falls back to the original file with its own
width and height. The `core.render_image` job makes them for every preset: it is queued on every
`MediaImage` save (`apps.core.signals`), so CMS, import, seed, product and logo images alike get
their renditions moments after they are stored.

AVIF needs an image library that can encode it; the Pillow wheels in uv.lock bundle libavif,
so the container image can. Where it cannot, AVIF is left out (WebP and JPEG remain) and the
system check core.W501 says so.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal

from django.forms.utils import flatatt
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


# Operations outside the presets that pages also use: the gallery carousel (`apps.cms.blocks`),
# the blog article image and author photo (`apps.cms.models`, JSON-LD). Made by the same job.
EXTRA_OPERATIONS = ("width-800", "width-1600", "width-1200", "fill-240x240")


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


def all_specs() -> list[str]:
    """Every rendition spec any preset can ask for (raster formats and SVG operations)."""
    formats = picture_formats()
    specs: set[str] = set()
    for name in PRESETS:
        specs.update(picture_specs(name, formats=formats))
        specs.update(picture_specs(name, formats=formats, svg=True))
    return sorted(specs)


#: The render job's groups in the order they are queued: the hero is the LCP image, so it comes
#: first; the carousel and blog operations (`EXTRA_OPERATIONS`) come last. Each value lists
#: preset names; "extras" is not a preset.
RENDER_GROUPS: dict[str, tuple[str, ...]] = {
    "hero": ("hero",),
    "content": ("content", "gallery"),
    "card": ("card",),
    "logo": ("logo",),
    "extras": (),
}


def group_specs(group: str, *, svg: bool = False) -> list[str]:
    """The rendition specs of one render group (an SVG: its resized operations)."""
    if group not in RENDER_GROUPS:
        raise ValueError(f"unknown render group {group!r}")
    formats = picture_formats()
    specs: set[str] = set(EXTRA_OPERATIONS) if group == "extras" else set()
    for name in RENDER_GROUPS[group]:
        specs.update(picture_specs(name, formats=formats, svg=svg))
    return sorted(specs)


def render_group(image: Any, group: str) -> None:
    """Make the renditions of one render group. Idempotent: Wagtail reuses a rendition that
    exists."""
    image.get_renditions(*group_specs(group, svg=bool(image.is_svg())))


def render_all(image: Any) -> None:
    """Make every rendition the pages can use for `image`: all groups, one after the other."""
    for group in RENDER_GROUPS:
        render_group(image, group)


def rendition_or_original(image: Any, spec: str) -> tuple[str, int | None, int | None]:
    """(url, width, height) of the existing rendition `spec`, else of the original file: a page
    never builds a rendition (see the module docstring)."""
    from wagtail.images.models import Filter

    wanted = Filter(spec=spec)
    found = image.find_existing_renditions(wanted).get(wanted)
    if found is not None:
        return str(found.url), found.width, found.height
    return str(image.file.url), image.width, image.height


def prefetch_renditions(images: Iterable[Any]) -> None:
    """Load the existing renditions of `images` in one query, so rendering a gallery or a grid
    of cards does not query per image. Images without renditions get an empty list."""
    by_pk: dict[Any, list[Any]] = {}
    for image in images:
        if image is not None:
            by_pk.setdefault(image.pk, []).append(image)
    if not by_pk:
        return
    first = next(iter(by_pk.values()))[0]
    found: dict[Any, list[Any]] = {pk: [] for pk in by_pk}
    for rendition in first.get_rendition_model().objects.filter(
        image_id__in=list(by_pk), filter_spec__in=all_specs()
    ):
        found[rendition.image_id].append(rendition)
    for pk, instances in by_pk.items():
        for image in instances:
            for rendition in found[pk]:
                rendition.image = image  # rendering reads it: no query per rendition
            image.prefetched_renditions = found[pk]


def render_picture(image: Any, name: str, **attrs: Any) -> SafeString:
    """`<picture>` HTML for a Wagtail image (see the module docstring).

    Only renditions that already exist are used: a page never builds one (CAT.4: the
    `core.render_image` job does, after the upload). A format with no rendition gets
    no `<source>`; without the fallback format (JPEG, PNG for logos; the resized SVG) the
    original file is shown with its own width and height.

    `priority=True` marks the LCP image: `fetchpriority="high"` and no lazy loading. Other
    attributes (`alt`, `class`) go on the `<img>`; `alt` defaults to "" (decorative).
    """
    from wagtail.images.models import Filter, Picture

    priority = bool(attrs.pop("priority", False))
    svg = bool(image.is_svg())
    specs = picture_specs(name, formats=picture_formats(), svg=svg)
    # Largest first: the <img> fallback takes the first rendition, and its width and height
    # (the intrinsic size the layout reserves) should be the largest, as `{% image %}` gave.
    filters = [Filter(spec=spec) for spec in reversed(specs)]
    existing = image.find_existing_renditions(*filters)
    renditions = {f.spec: existing[f] for f in filters if f in existing}
    if not svg:
        fallback = PRESETS[name].fallback
        if not any(f"format-{fallback}|" in spec for spec in renditions):
            renditions = {}
    img_attrs: dict[str, Any] = {"alt": attrs.pop("alt", "") or "", "sizes": PRESETS[name].sizes}
    if priority:
        img_attrs["fetchpriority"] = "high"
    else:
        img_attrs["loading"] = "lazy"
        img_attrs["decoding"] = "async"
    img_attrs.update({key: value for key, value in attrs.items() if value not in (None, "")})
    if not renditions:
        # The fallback has no srcset: the browser takes the one original, which can be large, for
        # every such image (24 cards = 24 originals). It is rare: `core.render_image` is queued on
        # every save and makes the renditions within moments, so only an image shown before the
        # worker ran (or after a failed job, CAT.24) gets here.
        original = {"src": image.file.url, "width": image.width, "height": image.height}
        del img_attrs["sizes"]
        img_attrs.update({k: v for k, v in original.items() if v})
        return format_html("<picture><img{}></picture>", flatatt(img_attrs))
    return format_html("{}", Picture(renditions, img_attrs))
