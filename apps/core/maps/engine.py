"""Pure rules of the tile proxy (EVT.14): validation, URLs, cache lifetime and the cache blob."""

import datetime as dt
import hashlib
import json
import re
from email.utils import parsedate_to_datetime

from . import TileKey

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_CACHE_SECONDS = 7 * 24 * 3600  # spec 15: at least 7 days
MAX_CACHE_SECONDS = 365 * 24 * 3600
PLACEHOLDERS = ("{z}", "{x}", "{y}")
_MAX_AGE = re.compile(r"(?:^|[\s,])(?:s-)?max-age=(\d+)", re.IGNORECASE)


def _integer(value: str) -> int | None:
    """A canonical non-negative decimal integer: ASCII digits only, no sign, no leading zero."""
    if not value or not value.isascii() or not value.isdigit() or len(value) > 9:
        return None
    if len(value) > 1 and value.startswith("0"):
        return None
    return int(value)


def parse_tile(z: str, x: str, y: str, *, min_zoom: int, max_zoom: int) -> TileKey | None:
    """The tile the three path parts name, or None: only integers, `min_zoom <= z <= max_zoom`
    and `0 <= x, y < 2**z`. Nothing else of the request ever reaches the upstream URL."""
    zoom, column, row = _integer(z), _integer(x), _integer(y)
    if zoom is None or column is None or row is None:
        return None
    if not min_zoom <= zoom <= max_zoom:
        return None
    if column >= 2**zoom or row >= 2**zoom:
        return None
    return TileKey(zoom, column, row)


def validate_template(template: str) -> str | None:
    """Why `template` cannot be an upstream URL template, or None when it can."""
    if not all(part in template for part in PLACEHOLDERS):
        return "needs the {z}, {x} and {y} placeholders"
    if not template.startswith("https://"):
        return "must be an https:// URL"
    return None


def upstream_url(template: str, key: TileKey) -> str:
    return template.replace("{z}", str(key.z)).replace("{x}", str(key.x)).replace("{y}", str(key.y))


def cache_path(template: str, key: TileKey, *, prefix: str = "map-tiles") -> str:
    """The storage name of a tile. The template is part of it (hashed, so a key in the URL is
    never written out): switching the upstream never serves another provider's tiles."""
    namespace = hashlib.sha256(template.encode()).hexdigest()[:12]
    return f"{prefix}/{namespace}/{key.z}/{key.x}/{key.y}.tile"


def cache_seconds(headers: dict[str, str], now: dt.datetime) -> int:
    """How long to keep a tile: the upstream's `Cache-Control: max-age` or `Expires` when that is
    longer than the 7-day minimum, capped at a year."""
    lowered = {name.lower(): value for name, value in headers.items()}
    seconds = 0
    control = lowered.get("cache-control", "")
    if "no-store" not in control.lower() and (match := _MAX_AGE.search(control)):
        seconds = int(match.group(1)[:12])
    elif expires := lowered.get("expires"):
        try:
            when = parsedate_to_datetime(expires)
        except (TypeError, ValueError):
            when = None
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.UTC)
            seconds = int((when - now).total_seconds())
    return min(max(seconds, MIN_CACHE_SECONDS), MAX_CACHE_SECONDS)


def is_png(body: bytes) -> bool:
    return body.startswith(PNG_MAGIC)


def pack(body: bytes, expires_at: dt.datetime) -> bytes:
    """The stored blob: one JSON header line with the expiry, then the image."""
    header = json.dumps({"expires": expires_at.isoformat()}).encode()
    return header + b"\n" + body


def unpack(blob: bytes) -> tuple[bytes, dt.datetime] | None:
    """The image and its expiry, or None for a blob that is not ours (a miss, never an error)."""
    header, separator, body = blob.partition(b"\n")
    if not separator:
        return None
    try:
        expires = dt.datetime.fromisoformat(json.loads(header)["expires"])
    except (ValueError, KeyError, TypeError):
        return None
    if expires.tzinfo is None or not is_png(body):
        return None
    return body, expires


def cache_control(max_age: int) -> str:
    return f"public, max-age={max_age}"
