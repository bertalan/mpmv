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
from apps.core.site_url import absolute_url

from . import sitemap_engine as engine
from . import sitemaps

XML = "application/xml"


def _require_indexing(request: HttpRequest) -> None:
    if is_noindex(request):
        raise Http404("Indexing is off.")


# Short and private: the answer depends on the host and on the site's indexing switch, so a
# shared cache must not keep it (a 404 while indexing is off carries no cache header).
_cache = cache_control(private=True, max_age=300)


def _chunks(request: HttpRequest, language: str) -> list[list[engine.Entry]]:
    return engine.chunk_entries(sitemaps.language_entries(language, request))


@require_safe
@_cache
def sitemap_index(request: HttpRequest) -> HttpResponse:
    """Every language's file; a language with more than 50,000 URLs (or 50 MB) lists its
    numbered parts `sitemap-<language>-<n>.xml` instead of one file."""
    _require_indexing(request)
    locations: list[str] = []
    for code in configured_languages():
        count = len(_chunks(request, code))
        if count == 1:
            names = [reverse("sitemap_language", kwargs={"language": code})]
        else:
            names = [
                reverse("sitemap_part", kwargs={"language": code, "part": n})
                for n in range(1, count + 1)
            ]
        locations += [absolute_url(request, name) for name in names]
    return HttpResponse(engine.index_xml(locations), content_type=XML)


@require_safe
@_cache
def sitemap_language(request: HttpRequest, language: str) -> HttpResponse:
    _require_indexing(request)
    if language not in configured_languages():
        raise Http404("No such language.")
    chunks = _chunks(request, language)
    if len(chunks) > 1:  # too big for one file: the index lists the parts
        raise Http404("Split into parts.")
    return HttpResponse(engine.urlset_xml(chunks[0]), content_type=XML)


@require_safe
@_cache
def sitemap_part(request: HttpRequest, language: str, part: int) -> HttpResponse:
    _require_indexing(request)
    if language not in configured_languages():
        raise Http404("No such language.")
    chunks = _chunks(request, language)
    if len(chunks) == 1 or not 1 <= part <= len(chunks):
        raise Http404("No such part.")
    return HttpResponse(engine.urlset_xml(chunks[part - 1]), content_type=XML)
