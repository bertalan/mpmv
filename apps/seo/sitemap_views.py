"""The sitemap index, the sitemaps per language and type, `llms.txt` (I18N.6, SEO.2).

With indexing off (the environment floor or the site's own switch, SEO.8) there is no sitemap:
404, and robots.txt carries no `Sitemap:` line.
"""

from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_safe

from apps.core.i18n import configured_languages, primary_language
from apps.core.indexing import is_noindex
from apps.core.site_url import absolute_url

from . import llms, sitemaps
from . import sitemap_engine as engine

XML = "application/xml"


def _require_indexing(request: HttpRequest) -> None:
    if is_noindex(request):
        raise Http404("Indexing is off.")


# Short and private: the answer depends on the host and on the site's indexing switch, so a
# shared cache must not keep it (a 404 while indexing is off carries no cache header).
_cache = cache_control(private=True, max_age=300)


def _chunks(request: HttpRequest, language: str, kind: str) -> list[list[engine.Entry]]:
    return engine.chunk_entries(sitemaps.kind_entries(kind, language, request))


def _file_names(language: str, kind: str, count: int) -> list[str]:
    if count == 1:
        return [reverse("sitemap_section", kwargs={"language": language, "kind": kind})]
    return [
        reverse("sitemap_part", kwargs={"language": language, "kind": kind, "part": str(n)})
        for n in range(1, count + 1)
    ]


@require_safe
@_cache
def sitemap_index(request: HttpRequest) -> HttpResponse:
    """Every language's files, one per type that has URLs (a language with no URL at all lists
    its empty pages file); a type with more than 50,000 URLs (or 50 MB) lists its numbered
    parts `sitemap-<language>-<type>-<n>.xml` instead of one file."""
    _require_indexing(request)
    locations: list[str] = []
    for code in configured_languages():
        found = False
        for kind in engine.KINDS:
            chunks = _chunks(request, code, kind)
            if not chunks[0]:
                continue
            found = True
            locations += [
                absolute_url(request, name) for name in _file_names(code, kind, len(chunks))
            ]
        if not found:
            locations.append(absolute_url(request, _file_names(code, "pages", 1)[0]))
    return HttpResponse(engine.index_xml(locations), content_type=XML)


def _checked(language: str, kind: str) -> None:
    if language not in configured_languages() or kind not in engine.KINDS:
        raise Http404("No such sitemap.")


@require_safe
@_cache
def sitemap_section(request: HttpRequest, language: str, kind: str) -> HttpResponse:
    _require_indexing(request)
    _checked(language, kind)
    chunks = _chunks(request, language, kind)
    if len(chunks) > 1:  # too big for one file: the index lists the parts
        raise Http404("Split into parts.")
    return HttpResponse(engine.urlset_xml(chunks[0]), content_type=XML)


@require_safe
@_cache
def sitemap_part(request: HttpRequest, language: str, kind: str, part: str) -> HttpResponse:
    _require_indexing(request)
    _checked(language, kind)
    chunks = _chunks(request, language, kind)
    if len(chunks) == 1 or not 1 <= int(part) <= len(chunks):
        raise Http404("No such part.")
    return HttpResponse(engine.urlset_xml(chunks[int(part) - 1]), content_type=XML)


@require_safe
@_cache
def llms_txt(request: HttpRequest, language: str | None = None) -> HttpResponse:
    """`/llms.txt` (the primary language) and `/llms-<language>.txt`."""
    _require_indexing(request)
    language = language or primary_language()
    if language not in configured_languages():
        raise Http404("No such language.")
    return HttpResponse(llms.llms_txt(language, request), content_type="text/plain; charset=utf-8")
