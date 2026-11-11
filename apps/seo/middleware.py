"""`X-Robots-Tag: noindex, nofollow` on every response when indexing is forced off (SEO.8)."""

from collections.abc import Callable

from django.http import HttpRequest
from django.http.response import HttpResponseBase

from apps.core.indexing import NOINDEX, is_noindex


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
