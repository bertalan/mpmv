"""Render each request with its site's theme (THM.1, spec 14 §14.3)."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponseBase

from .services import use_theme


class ThemeMiddleware:
    """Makes the request's theme the one the template loader uses while the response is built.

    The theme is resolved lazily, on the first themed template: API and admin requests never
    look up the site setting.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        with use_theme(request):
            return self.get_response(request)
