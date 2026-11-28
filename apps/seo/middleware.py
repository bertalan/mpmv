"""SEO middleware: forced noindex (SEO.8) and the anonymous page cache (SEO.6)."""

import datetime as dt
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.middleware.csp import get_nonce
from django.utils.cache import patch_vary_headers

from apps.core.indexing import NOINDEX, is_noindex

from . import page_cache
from . import page_cache_engine as engine


class ForceNoindexMiddleware:
    """`X-Robots-Tag: noindex` when the floor or the site's SEO setting says so.

    Defence in depth next to the proxy's header; also covers early returns and 404s. The verdict
    is read per request (no server-side page cache holds it), so a toggle change applies to the
    next response of every page.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        if is_noindex(request):
            response["X-Robots-Tag"] = NOINDEX
        return response


class PageCacheMiddleware:
    """Serve a CMS page to an anonymous visitor from the cache (SEO.6, docs/dev/page-cache.md).

    Listed after LocaleMiddleware (the key needs the language) and outside CSRF, authentication
    and messages, so a hit renders no page (only the indexing verdict query runs). Only the
    Wagtail routes are cached, and only a response that holds nothing per request:
    `engine.response_skip_reason` refuses a page that
    generated a CSP nonce, asked for a CSRF token, set a cookie or read the session. The CSP
    header, X-Robots-Tag, HSTS and the rest are added by the middleware around this one, for each
    request, so a hit carries a fresh nonce-less header and the current indexing state.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if not getattr(settings, "PAGE_CACHE_ENABLED", False):
            return self.get_response(request)
        reason = engine.request_bypass_reason(
            method=request.method or "",
            query_string=request.META.get("QUERY_STRING", ""),
            path=request.path,
            cookie_names=request.COOKIES.keys(),
            bypass_cookies=self._bypass_cookies(),
            authorization="HTTP_AUTHORIZATION" in request.META,
        )
        if reason:
            response = self.get_response(request)
            response["X-Page-Cache"] = "BYPASS"
            return response
        key = page_cache.try_key(request)  # before the render: see `page_cache.try_key`
        if key is None:
            return self.get_response(request)
        stored = page_cache.lookup(request, key=key)
        if stored is not None:
            response = stored.to_response()
            self._mark_shared(response, "HIT")
            return response
        response = self.get_response(request)
        response["X-Page-Cache"] = "MISS"
        if self._is_cms_page(request) and self._store(request, response, key):
            self._mark_shared(response, "MISS")
        return response

    @staticmethod
    def _bypass_cookies() -> frozenset[str]:
        return frozenset(
            {
                settings.SESSION_COOKIE_NAME,
                settings.CSRF_COOKIE_NAME,
                *getattr(settings, "PAGE_CACHE_BYPASS_COOKIES", ()),
            }
        )

    @staticmethod
    def _is_cms_page(request: HttpRequest) -> bool:
        match = getattr(request, "resolver_match", None)
        names = getattr(settings, "PAGE_CACHE_URL_NAMES", ())
        return match is not None and match.url_name in names

    @staticmethod
    def _store(request: HttpRequest, response: HttpResponseBase, key: str) -> bool:
        if response.streaming:
            return False
        if not getattr(request, "_page_cache_unrestricted", False):
            # The `before_serve_page` hook (wagtail_hooks) clears a page with no view restriction
            # on it or its ancestors; a page it did not clear (restricted, or not a page) is not
            # stored, and a hit would skip the restriction check.
            return False
        content = getattr(response, "content", b"")
        cache_control = response.get("Cache-Control", "")
        session = getattr(request, "session", None)
        nonce = get_nonce(request)
        reason = engine.response_skip_reason(
            status=response.status_code,
            content_type=response.get("Content-Type", ""),
            streaming=False,
            sets_cookie=bool(response.cookies),
            cache_control=cache_control,
            # Only a change counts: this request carried no session cookie, so reading the
            # (empty) session, as the language middleware does, varies nothing.
            session_modified=bool(getattr(session, "modified", False)),
            nonce_used=bool(nonce),  # a LazyNonce is falsy until it has been generated
            csrf_used=bool(request.META.get("CSRF_COOKIE_NEEDS_UPDATE")),
            body=bytes(content),
            max_bytes=settings.PAGE_CACHE_MAX_BYTES,
        )
        if reason:
            return False
        seconds = engine.lifetime(
            settings.PAGE_CACHE_SECONDS, cache_control, bytes(content), dt.datetime.now(dt.UTC)
        )
        if seconds <= 0:
            return False
        page_cache.store(request, response, timeout=seconds, key=key)
        return True

    @staticmethod
    def _mark_shared(response: HttpResponseBase, status: str) -> None:
        """A shared cache may keep an anonymous page but must ask us again (we do the purging)."""
        response["X-Page-Cache"] = status
        response["Cache-Control"] = "public, max-age=0, must-revalidate"
        patch_vary_headers(response, ("Cookie", "Accept-Language"))
