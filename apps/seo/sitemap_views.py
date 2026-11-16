"""The sitemap index and one sitemap per language (I18N.6). Thin adapters over `sitemaps`.

With indexing off (the environment floor or the site's own switch, SEO.8) there is no sitemap:
404, and robots.txt carries no `Sitemap:` line.
"""

from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_safe

from apps.core.i18n import configured_languages
from apps.core.indexing import is_noindex

from . import sitemap_engine as engine
from . import sitemaps

XML = "application/xml"


def _require_indexing(request: HttpRequest) -> None:
    if is_noindex(request):
        raise Http404("Indexing is off.")


@require_safe
@cache_control(max_age=3600)
def sitemap_index(request: HttpRequest) -> HttpResponse:
    _require_indexing(request)
    locations = [
        request.build_absolute_uri(reverse("sitemap_language", kwargs={"language": code}))
        for code in configured_languages()
    ]
    return HttpResponse(engine.index_xml(locations), content_type=XML)


@require_safe
@cache_control(max_age=3600)
def sitemap_language(request: HttpRequest, language: str) -> HttpResponse:
    _require_indexing(request)
    if language not in configured_languages():
        raise Http404("No such language.")
    entries = sitemaps.language_entries(language, request)
    return HttpResponse(engine.urlset_xml(entries), content_type=XML)
