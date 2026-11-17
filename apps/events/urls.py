"""Public event resources Wagtail's own URL pattern cannot reach (EVT.6).

Wagtail serves slash-terminated paths only, so a file-like route of a page (`calendar.ics`) needs
its own pattern. The view hands the path to Wagtail's `serve`, which routes it through the
page tree to the `RoutablePageMixin` route of the event (hidden events are a 404 there). EVT.9
adds the feed routes of the index pages (`feed.rss`, `calendar.ics`) to the same pattern.
Mounted inside `i18n_patterns`, before the CMS catch-all.
"""

from django.http import HttpRequest, HttpResponse
from django.urls import re_path
from django.views.decorators.http import require_GET
from wagtail.views import serve


@require_GET
def event_resource(request: HttpRequest, path: str) -> HttpResponse:
    response: HttpResponse = serve(request, path)
    return response


urlpatterns = [
    re_path(r"^(?P<path>(?:[\w\-]+/)+calendar\.ics)$", event_resource, name="events_resource"),
]
