"""The moderation guard on wagtail-localize's admin views (EVT.3 part 2/2, spec 15 "Hiding").

"Update translations", "Translate" and the string editor publish or change an event's
translations without Wagtail's `before_edit_page` hook, so the `wagtail_hooks` guard never sees
them. This middleware applies the same rules (`services.admin_localize_refused`) before those
views run. `EventPage.publish` refusing a hidden page is the last line behind it.
"""

from collections.abc import Callable
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse

from .services import admin_localize_refused, localize_view_event

LOCALIZE = "wagtail_localize"
# The views that publish when the request asks to: the form's "Publish" box, and a new
# translation of a live source (wagtail-localize publishes it straight away).
UPDATE = f"{LOCALIZE}:update_translations"
SUBMIT = f"{LOCALIZE}:submit_page_translation"


class LocalizeEventGuardMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., Any],
        view_args: tuple[Any, ...],
        view_kwargs: dict[str, Any],
    ) -> None:
        match = request.resolver_match
        if match is None or LOCALIZE not in match.namespaces:
            return
        event = localize_view_event(view_kwargs)
        if event is None:
            return
        publishes = request.method == "POST" and (
            (match.view_name == UPDATE and bool(request.POST.get("publish_translations")))
            or (match.view_name == SUBMIT and event.live)
        )
        if admin_localize_refused(request.user, event, publishes=publishes):
            raise PermissionDenied
