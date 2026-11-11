"""`X-Robots-Tag: noindex, nofollow` on every response when indexing is forced off (SEO.8)."""

from collections.abc import Callable

from django.http import HttpRequest
from django.http.response import HttpResponseBase

from apps.core.indexing import NOINDEX, is_noindex_forced


class ForceNoindexMiddleware:
    """Defence in depth next to the proxy's header; also covers early returns and 404s."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        if is_noindex_forced():
            response["X-Robots-Tag"] = NOINDEX
        return response
