"""The pure rules of the anonymous page cache (SEO.6): no ORM, no cache, no request objects.

What may be served from the cache, what may be stored, under which key and for how long. The
middleware (`apps.seo.middleware`) and the store (`apps.seo.page_cache`) apply them.

The hard rule: a cached page holds nothing per request. No CSP nonce, no CSRF token, no cookie,
nothing that read the session or the user. `response_skip_reason` enforces it twice, on what the
request did (nonce generated, CSRF token asked for, session read, cookie set) and on what the body
contains.
"""

import datetime as dt
import hashlib
import re
from collections.abc import Collection
from dataclasses import dataclass

from apps.core.indexing import PRIVATE_PATHS

KEY_PREFIX = "mpmv:page:"

_NONCE_ATTR = re.compile(rb"\snonce\s*=", re.IGNORECASE)
_CSRF_FIELD = b"csrfmiddlewaretoken"
_COUNTDOWN_TARGET = re.compile(rb'data-target="([^"]+)"')
_DIRECTIVE = re.compile(r"\b(max-age|s-maxage)=(\d+)", re.IGNORECASE)
_UNSTORABLE = re.compile(r"\b(private|no-store|no-cache|max-age=0|s-maxage=0)\b", re.IGNORECASE)


@dataclass(frozen=True)
class KeyParts:
    """Everything that changes the HTML of a page for an anonymous visitor.

    The site follows from the host, the language from the URL prefix (or the negotiated language
    on unprefixed paths), the theme from the site (a theme change drops the generation). Device
    class is not a variant until the renditions part (SEO.6 2/n) serves different markup.
    """

    scheme: str
    host: str
    path: str
    language: str
    noindex: bool
    generation: str


def cache_key(parts: KeyParts) -> str:
    raw = "\x00".join(
        (
            parts.generation,
            parts.scheme,
            parts.host.lower(),
            parts.path,
            parts.language,
            "noindex" if parts.noindex else "index",
        )
    )
    return KEY_PREFIX + hashlib.sha256(raw.encode()).hexdigest()


def request_bypass_reason(
    *,
    method: str,
    query_string: str,
    path: str,
    cookie_names: Collection[str],
    bypass_cookies: Collection[str],
    authorization: bool,
) -> str | None:
    """Why this request never reads from or writes to the cache, or None."""
    if method != "GET":
        return "method"
    if query_string:
        return "query string"
    if authorization:
        return "authorization"
    if any(name in bypass_cookies for name in cookie_names):
        return "cookie"
    if any(path.startswith(prefix) for prefix in PRIVATE_PATHS):
        return "private area"
    return None


def response_skip_reason(
    *,
    status: int,
    content_type: str,
    streaming: bool,
    sets_cookie: bool,
    cache_control: str,
    session_modified: bool,
    nonce_used: bool,
    csrf_used: bool,
    body: bytes,
    max_bytes: int,
) -> str | None:
    """Why this response is not stored, or None. See the module docstring for the hard rule."""
    if status != 200:
        return "status"
    if not content_type.lower().startswith("text/html"):
        return "content type"
    if streaming:
        return "streaming"
    if sets_cookie:
        return "cookie"
    if session_modified:
        return "session"
    if nonce_used or _NONCE_ATTR.search(body):
        return "nonce"
    if csrf_used or _CSRF_FIELD in body:
        return "csrf"
    if _UNSTORABLE.search(cache_control):
        return "cache-control"
    if len(body) > max_bytes:
        return "too large"
    return None


def lifetime(default: int, cache_control: str, body: bytes, now: dt.datetime) -> int:
    """Seconds a page may stay cached; 0 means do not store.

    The default, shortened by the view's own `max-age` / `s-maxage` and by the nearest running
    launch countdown in the body (`data-target`, spec 10 §10.2): a cached page is never served
    past the target. Only ever shortens.
    """
    seconds = default
    for _name, value in _DIRECTIVE.findall(cache_control):
        seconds = min(seconds, int(value))
    if b"data-countdown" in body:
        for raw in _COUNTDOWN_TARGET.findall(body):
            target = _parse_target(raw.decode(errors="replace"))
            if target is not None and target > now:
                seconds = min(seconds, int((target - now).total_seconds()))
    return max(seconds, 0)


def _parse_target(value: str) -> dt.datetime | None:
    try:
        target = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return target if target.tzinfo is not None else None
