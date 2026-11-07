"""Nonces for Wagtail's date and time pickers in the editors' admin (CMS.1, see `csp`).

Listed right after Django's ContentSecurityPolicyMiddleware, so it handles the response first:
using the nonce here puts it in the CSP header that middleware writes next.
"""

from collections.abc import Callable

from django.http import HttpRequest
from django.http.response import HttpResponse, HttpResponseBase
from django.middleware.csp import get_nonce
from django.urls import NoReverseMatch, reverse

from .csp import nonce_picker_scripts


def _in_wagtail_admin(request: HttpRequest) -> bool:
    try:
        return request.path.startswith(reverse("wagtailadmin_home"))
    except NoReverseMatch:  # a URLconf without the Wagtail admin (some tests)
        return False


class WagtailAdminPickerNonceMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        nonce = get_nonce(request)
        if (
            nonce is None
            or not isinstance(response, HttpResponse)
            or not response.get("Content-Type", "").startswith("text/html")
            or not _in_wagtail_admin(request)
        ):
            return response
        html, changed = nonce_picker_scripts(response.content.decode(response.charset), str(nonce))
        if changed:
            response.content = html.encode(response.charset)
        return response
