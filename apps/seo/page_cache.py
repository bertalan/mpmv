"""Store of the anonymous page cache (SEO.6): I/O around `page_cache_engine`.

One cache alias (`pagecache`, shared by every gunicorn worker and the task worker) holds one entry
per page variant plus a *generation* token. The generation is part of every key, so
`invalidate()` drops the whole cache with one write and works across processes: new requests
build new keys, the old entries expire on their own. A cache that is down or unwritable is a
miss, never an error for the visitor.
"""

import logging
import uuid
from dataclasses import dataclass

from django.core.cache import caches
from django.core.cache.backends.base import BaseCache
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase

from apps.core.indexing import is_noindex_forced

from .page_cache_engine import KeyParts, cache_key

logger = logging.getLogger(__name__)

CACHE_ALIAS = "pagecache"
GENERATION_KEY = "mpmv:page:generation"

# Headers kept with a stored page. Everything else is rebuilt for each request by the middleware
# stack (CSP with its nonce, HSTS, Permissions-Policy, X-Robots-Tag when forced, Content-Language,
# Vary), so a hit never replays a per-request value.
STORED_HEADERS = ("Content-Type", "X-Robots-Tag", "Link")


@dataclass(frozen=True)
class StoredPage:
    status: int
    content: bytes
    headers: tuple[tuple[str, str], ...]

    def to_response(self) -> HttpResponse:
        response = HttpResponse(self.content, status=self.status)
        for name, value in self.headers:
            response[name] = value
        return response


def _cache() -> BaseCache:
    return caches[CACHE_ALIAS]


def _generation() -> str:
    cache = _cache()
    generation = cache.get(GENERATION_KEY)
    if generation is None:
        cache.add(GENERATION_KEY, uuid.uuid4().hex, None)  # a concurrent worker may win: re-read
        generation = cache.get(GENERATION_KEY) or "0"
    return str(generation)


def invalidate() -> None:
    """Drop every cached page, in every process (publish, unpublish, move, settings changes)."""
    try:
        _cache().set(GENERATION_KEY, uuid.uuid4().hex, None)
    except Exception:
        logger.warning("page cache: could not invalidate", exc_info=True)


def key_for(request: HttpRequest) -> str:
    return cache_key(
        KeyParts(
            scheme="https" if request.is_secure() else "http",
            host=request.get_host(),
            path=request.path,
            language=str(getattr(request, "LANGUAGE_CODE", "")),
            noindex=is_noindex_forced(),
            generation=_generation(),
        )
    )


def lookup(request: HttpRequest) -> StoredPage | None:
    try:
        found = _cache().get(key_for(request))
    except Exception:
        logger.warning("page cache: lookup failed", exc_info=True)
        return None
    return found if isinstance(found, StoredPage) else None


def store(request: HttpRequest, response: HttpResponseBase, *, timeout: int) -> None:
    content = getattr(response, "content", b"")
    headers = tuple((name, response[name]) for name in STORED_HEADERS if response.has_header(name))
    page = StoredPage(status=response.status_code, content=bytes(content), headers=headers)
    try:
        _cache().set(key_for(request), page, timeout)
    except Exception:
        logger.warning("page cache: store failed", exc_info=True)
