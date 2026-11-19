"""The help dialog's body (ARC.32): a small GET endpoint for signed-in users.

The pages carry no help text: the button's script fetches it here, so what is cached or public
never holds it. The answer is an HTML fragment, in the language the page asked for, and is never
stored by a browser or a shared cache.
"""

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import translation
from django.utils.cache import add_never_cache_headers, patch_cache_control
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from apps.core.i18n import configured_languages
from apps.core.site_url import absolute_url

from . import engine, help_guide
from . import help as help_lookup


def _no_store(response: HttpResponse) -> HttpResponse:
    add_never_cache_headers(response)
    patch_cache_control(response, no_store=True, private=True)
    return response


@require_GET
def help_entry(request: HttpRequest) -> HttpResponse:
    if not (request.user.is_authenticated and request.user.is_active):
        return _no_store(HttpResponse(status=403))
    asked = request.GET.get("lang", "")
    language = asked if asked in configured_languages() else translation.get_language()
    language = language or settings.LANGUAGE_CODE
    route = engine.help_route_name(request.GET.get("route"))
    page_type = engine.help_page_type(request.GET.get("page_type"))
    with translation.override(language):
        content = help_lookup.help_for(route, page_type, language)
        guide_url = ""
        if content and content.vendor_guide:
            guide_url = engine.help_guide_url(settings.MPMV_VENDOR_GUIDE_URL)
        response = render(
            request,
            "components/help_entry.html",
            {
                "help": content,
                "vendor_guide_url": guide_url,
                "guide_url": help_guide.section_url(engine.ROUTE_GUIDE.get(route, "")),
            },
        )
    return _no_store(response)


def _index(request: HttpRequest, audience: str) -> HttpResponse:
    language = translation.get_language() or settings.LANGUAGE_CODE
    return render(
        request,
        "cms/help_guide_index.html",
        {
            "audience": audience,
            "sections": help_guide.sections(audience, language),
            "canonical_url": absolute_url(request, request.path),
            "alternates": [],
        },
    )


def _section(request: HttpRequest, audience: str, section: str) -> HttpResponse:
    language = translation.get_language() or settings.LANGUAGE_CODE
    page = help_guide.section(audience, section, language)
    if page is None:
        raise Http404("No such help section")
    canonical, alternates = help_guide.section_links(section, language, request, audience)
    return render(
        request,
        "cms/help_guide_section.html",
        {
            "audience": audience,
            "guide": page,
            "sections": help_guide.sections(audience, language),
            "canonical_url": canonical,
            "alternates": alternates,
        },
    )


@require_GET
@cache_control(public=True, max_age=300)
def customer_help(request: HttpRequest) -> HttpResponse:
    """The customer help centre: the sections, in order (ARC.37)."""
    return _index(request, "customer")


@require_GET
@cache_control(public=True, max_age=300)
def customer_help_section(request: HttpRequest, section: str) -> HttpResponse:
    """One section of the customer help (ARC.37), from its help file."""
    return _section(request, "customer", section)


@require_GET
@cache_control(public=True, max_age=300)
def vendor_help(request: HttpRequest) -> HttpResponse:
    """The seller guide: the sections, in order (ARC.37)."""
    return _index(request, "vendor")


@require_GET
@cache_control(public=True, max_age=300)
def vendor_help_section(request: HttpRequest, section: str) -> HttpResponse:
    """One section of the seller guide (ARC.37), from its help file."""
    return _section(request, "vendor", section)
