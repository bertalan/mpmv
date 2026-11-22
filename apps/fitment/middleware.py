"""FIT.6: clear the garage cookie on the sign-out response when `flag_sign_out` asked for it."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from . import garage_merge


class GarageSignOutMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if getattr(request, garage_merge.CLEAR_ATTR, False):
            garage_merge.clear_cookie(response)
        return response
