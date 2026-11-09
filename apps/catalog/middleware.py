"""Redirects of old URLs: product slugs (CAT.5) and the legacy redirect rules (CAT.6).

`OldProductSlugRedirectMiddleware`: a 301 from a product's old slug to its current one (CAT.5,
spec 03 §3.2). Acts only on a 404 for a GET or HEAD of a product path (`/product/{slug}/`,
`/it/prodotto/{slug}/`…): a page that exists always wins, so a product serving its frozen legacy
URL in legacy mode (Q-91, MIG.11) is never redirected. The query string is kept.

`LegacyRedirectMiddleware`: the staff-managed `Redirect` rules (CAT.6, spec 03 §3.1), also on a
404 for a GET or HEAD only, after the old-slug redirect: a 301 to the rule's target in the path's
language, or a 410. A GET with the legacy `?add-to-cart=` goes to the product page (302) before
any view runs, and never adds to the cart.
"""

from collections.abc import Callable

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponseGone,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)
from django.http.response import HttpResponseBase

from . import engine, services, slugs
from .models import Redirect

SAFE_METHODS = ("GET", "HEAD")


class OldProductSlugRedirectMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        if response.status_code != 404 or request.method not in SAFE_METHODS:
            return response
        found = slugs.parse_product_path(
            request.path_info,
            languages=[code for code, _ in settings.LANGUAGES],
            default_language=settings.LANGUAGE_CODE,
        )
        if found is None:
            return response
        target = services.old_slug_redirect(*found)
        if target is None:
            return response
        query = request.META.get("QUERY_STRING", "")
        return HttpResponsePermanentRedirect(f"{target}?{query}" if query else target)


class LegacyRedirectMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.method in SAFE_METHODS and services.ADD_TO_CART_PARAMETER in request.GET:
            return HttpResponseRedirect(self._add_to_cart_location(request))
        response = self.get_response(request)
        if response.status_code != 404 or request.method not in SAFE_METHODS:
            return response
        rule = services.resolve_legacy_path(request.path_info)
        if rule is None:
            return response
        if rule.code == Redirect.Code.GONE:
            return HttpResponseGone()
        target = services.redirect_target(rule, request.path_info)
        if target is None:
            return response
        query = request.META.get("QUERY_STRING", "")
        return HttpResponsePermanentRedirect(engine.with_query(target, query))

    @staticmethod
    def _add_to_cart_location(request: HttpRequest) -> str:
        """The product page, else the same page without the parameter (never another site)."""
        value = request.GET.get(services.ADD_TO_CART_PARAMETER, "")
        target = services.add_to_cart_target(request.path_info, value)
        if target is not None:
            return target
        path = request.path
        if engine.redirect_path_error(path, allow_query=False) is not None:
            return "/"
        rest = request.GET.copy()
        del rest[services.ADD_TO_CART_PARAMETER]
        return engine.with_query(path, rest.urlencode())
