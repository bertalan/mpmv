"""FIT.6: clear the garage cookie on the sign-out response when `flag_sign_out` asked for it.
FIT.11: a response built with an active garage (`garage_nodes.active_node`) is `private`."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_cache_control

from . import garage_merge


class GarageSignOutMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if getattr(request, garage_merge.CLEAR_ATTR, False):
            garage_merge.clear_cookie(response)
        if getattr(request, "_garage_active_node", None) is not None:
            # Personalised by an active garage (FIT.11, spec 17 §17.7): no shared cache.
            patch_cache_control(response, private=True)
        return response
