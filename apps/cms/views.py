"""Thin views around Wagtail's page serving (CMS.1, I18N.21)."""

from django.http import Http404, HttpRequest
from django.http.response import HttpResponseBase
from django.utils import translation
from wagtail.models import Page
from wagtail.views import serve

from apps.catalog import services as catalog_services
from apps.core.i18n import FALLBACK_ATTR, primary_language


def home(request: HttpRequest) -> HttpResponseBase:
    """The active language's home page (`/`, `/it/`, `/de/`), served by Wagtail."""
    response: HttpResponseBase = serve(request, "")
    return response


def serve_page(request: HttpRequest, path: str) -> HttpResponseBase:
    """Wagtail's page view, with the primary-language fallback (I18N.21, spec 09 §9.2).

    A path with no live page in the requested language is looked up in the primary language's
    tree and served in place, the interface staying in the requested language. Wagtail's own
    `serve` still runs, so view restrictions and the page hooks apply to the page shown; a draft
    or private page is never found (routing only returns live pages). A page type that sets
    `language_fallback = False` (events, legal pages) keeps its 404. A redirect rule for the
    requested path wins over the fallback: the 404 lets the redirect middleware apply it.
    """
    language = translation.get_language()
    primary = primary_language()
    if language == primary or Page.route_for_request(request, path) is not None:
        response: HttpResponseBase = serve(request, path)
        return response
    if catalog_services.resolve_legacy_path(request.path_info) is not None:
        raise Http404
    del request._wagtail_route_for_request  # type: ignore[attr-defined]  # Wagtail's cache
    with translation.override(primary):
        route = Page.route_for_request(request, path)
    if route is None or not getattr(route[0].specific_class, "language_fallback", True):
        request._wagtail_route_for_request = route  # type: ignore[attr-defined]
        raise Http404
    setattr(request, FALLBACK_ATTR, primary)
    response = serve(request, path)  # reads the route cached just above
    return response
