"""A 301 from a product's old slug to its current one (CAT.5, spec 03 §3.2).

Acts only on a 404 for a GET or HEAD of a product path (`/product/{slug}/`,
`/it/prodotto/{slug}/`…): a page that exists always wins, so a product serving its frozen legacy
URL in legacy mode (Q-91, MIG.11) is never redirected. The query string is kept.
"""

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponsePermanentRedirect
from django.http.response import HttpResponseBase

from . import services, slugs


class OldProductSlugRedirectMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        if response.status_code != 404 or request.method not in ("GET", "HEAD"):
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
