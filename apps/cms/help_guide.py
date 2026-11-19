"""Guide pages generated from the help files (ARC.37): the customer help and the seller guide.

A guide page is a `HelpEntry` of kind `guide`, keyed `<audience>:<section>` (the registry is
`engine.GUIDE_SECTIONS`), made by `sync_help` from `help/<lang>/guide/<audience>__<section>.md`.
The pages are read-only views over those rows: the files win, nothing is edited in the admin.
A page is shown in the visitor's language, else in the primary one (never another language).
Operator facts and links are filled from the placeholder allow-list when the page is shown.
"""

from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils import translation
from wagtail.rich_text import expand_db_html

from apps.core.i18n import configured_languages, hreflang_alternates, primary_language
from apps.core.site_url import absolute_url

from . import engine, help_files
from .models import HELP_FEATURES, HelpEntry

# The URL name of an audience's section page; the vendor guide lives under `help-centre/sellers/`.
URL_NAMES = {"customer": "customer_help_section", "vendor": "vendor_help_section"}


@dataclass(frozen=True)
class GuidePage:
    audience: str
    section: str  # the URL slug
    title: str
    html: str
    url: str = ""


def _page(entry: HelpEntry, language: str) -> GuidePage:
    audience, _sep, slug = entry.key.partition(":")
    html = str(expand_db_html(engine.sanitise_rich_text(entry.body, HELP_FEATURES)))
    with translation.override(language):  # link placeholders carry the page's language prefix
        return GuidePage(
            audience=audience,
            section=slug,
            title=help_files.render_placeholders(entry.title),
            html=help_files.render_placeholders(html),
            url=reverse(URL_NAMES[audience], args=[slug]),
        )


def sections(audience: str, language: str) -> list[GuidePage]:
    """The pages of `audience` in registry order, each in `language` or else the primary one."""
    wanted = [engine.guide_key(audience, s) for s in engine.GUIDE_SECTIONS.get(audience, ())]
    primary = settings.LANGUAGE_CODE
    rows = HelpEntry.objects.filter(
        kind=engine.HELP_GUIDE, key__in=wanted, locale__language_code__in={language, primary}
    ).select_related("locale")
    by_key: dict[tuple[str, str], HelpEntry] = {
        (row.key, row.locale.language_code): row for row in rows
    }
    pages = []
    for key in wanted:
        entry = by_key.get((key, language)) or by_key.get((key, primary))
        if entry is not None:
            pages.append(_page(entry, language))
    return pages


def section(audience: str, slug: str, language: str) -> GuidePage | None:
    if slug not in engine.GUIDE_SECTIONS.get(audience, ()):
        return None
    return next((p for p in sections(audience, language) if p.section == slug), None)


def section_url(guide_key: str) -> str:
    """The address of the page a guide key names, in the active language; "" for none."""
    audience, _sep, slug = guide_key.partition(":")
    if not engine.guide_key_valid(guide_key):
        return ""
    return reverse(URL_NAMES[audience], args=[slug])


def own_languages(audience: str, slug: str) -> list[str]:
    """The configured languages whose page for this section has its own file, in the configured
    order. A language without one shows the primary-language text (never listed, never indexed)."""
    have = set(
        HelpEntry.objects.filter(
            kind=engine.HELP_GUIDE, key=engine.guide_key(audience, slug)
        ).values_list("locale__language_code", flat=True)
    )
    return [code for code in configured_languages() if code in have]


def section_path(slug: str, language: str, audience: str = "customer") -> str:
    with translation.override(language):
        return reverse(URL_NAMES[audience], args=[slug])


def section_links(
    slug: str, language: str, request: HttpRequest, audience: str = "customer"
) -> tuple[str, list[tuple[str, str]]]:
    """The canonical URL and the `hreflang` set of a help page. A language without its
    own file shows the primary text, so its canonical is the primary page (no duplicate); the
    alternates list only the languages that have their own file."""
    own = own_languages(audience, slug)
    primary = primary_language()
    found = {code: absolute_url(request, section_path(slug, code, audience)) for code in own}
    shown = language if language in own else primary
    canonical = found.get(shown) or absolute_url(request, section_path(slug, shown, audience))
    return canonical, hreflang_alternates(found)
