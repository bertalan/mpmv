"""Synthetic placeholder images for the demo seed (ARC.10): abstract shapes drawn with Pillow.

No photo, font or downloaded file is used, so the images carry no third-party rights; the same
key always draws the same WebP bytes.
"""

import io
import random
import zlib

from PIL import Image, ImageDraw

WIDTH = 1200
HEIGHT = 675  # 16:9


def _shade(colour: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    r, g, b = (max(0, min(255, round(c * factor))) for c in colour)
    return (r, g, b)


def placeholder_webp(key: str, colour: tuple[int, int, int], *, scale: float = 1.0) -> bytes:
    """A 1200x675 WebP (times `scale`) of soft circles and bands on `colour`, the same for the
    same `key` and scale."""
    rng = random.Random(zlib.crc32(key.encode()))  # noqa: S311 - drawing, not security
    image = Image.new("RGB", (WIDTH, HEIGHT), _shade(colour, 1.15))
    draw = ImageDraw.Draw(image)
    for band in range(6):
        top = band * HEIGHT // 6
        draw.rectangle((0, top, WIDTH, top + HEIGHT // 12), fill=_shade(colour, 1.05 + band / 60))
    for _ in range(9):
        radius = rng.randint(60, 220)
        x, y = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=_shade(colour, rng.uniform(0.6, 1.3)),
        )
    draw.rectangle((40, HEIGHT - 110, 420, HEIGHT - 40), fill=(255, 255, 255))
    draw.rectangle((60, HEIGHT - 90, 400, HEIGHT - 60), fill=_shade(colour, 0.7))
    if scale != 1.0:
        image = image.resize((round(WIDTH * scale), round(HEIGHT * scale)))
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=80, method=4)
    return buffer.getvalue()


def logo_png(colour: tuple[int, int, int], size: int) -> bytes:
    """SEO.18: a square PNG of the site-icon mark (THM.38): a rounded square in `colour` with a
    white "M" drawn as a polygon, so no font file is needed."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=(*colour, 255))
    u = size / 16  # the mark on a 16-unit grid
    points = [
        (3, 12),
        (3, 4),
        (5, 4),
        (8, 8),
        (11, 4),
        (13, 4),
        (13, 12),
        (11, 12),
        (11, 7.5),
        (8, 11),
        (5, 7.5),
        (5, 12),
    ]
    draw.polygon([(x * u, y * u) for x, y in points], fill=(255, 255, 255, 255))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
