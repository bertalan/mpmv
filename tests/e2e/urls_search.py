"""URLconf for the `ride` overlay e2e (THM.15 part 2): the root URLconf plus a stand-in search page.

The search page itself is CAT.9; until it exists the header's search link has no route to point
at, so the overlay e2e adds one named `search` (the name the ride header looks up).
"""

from django.conf.urls.i18n import i18n_patterns
from django.http import HttpRequest, HttpResponse
from django.urls import path

from mpmv.urls import handler400, handler500
from mpmv.urls import urlpatterns as root_patterns

__all__ = ["handler400", "handler500", "urlpatterns"]


def results(request: HttpRequest) -> HttpResponse:
    return HttpResponse("<!doctype html><title>Search</title><h1>Search results</h1>")


urlpatterns = [*i18n_patterns(path("search/", results, name="search")), *root_patterns]
