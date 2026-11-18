"""Guide pages generated from the help files (ARC.37): the customer help, and the vendor guide
that can reuse the same mechanism.

A guide page is a `HelpEntry` of kind `guide`, keyed `<audience>:<section>` (the registry is
`engine.GUIDE_SECTIONS`), made by `sync_help` from `help/<lang>/guide/<audience>__<section>.md`.
The pages are read-only views over those rows: the files win, nothing is edited in the admin.
A page is shown in the visitor's language, else in the primary one (never another language).
Operator facts and links are filled from the placeholder allow-list when the page is shown.
"""

from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse
from django.utils import translation
from wagtail.rich_text import expand_db_html

from . import engine, help_files
from .models import HELP_FEATURES, HelpEntry


@dataclass(frozen=True)
class GuidePage:
    audience: str
    section: str  # the URL slug
    title: str
    html: str


def _page(entry: HelpEntry, language: str) -> GuidePage:
    audience, _sep, slug = entry.key.partition(":")
    html = str(expand_db_html(engine.sanitise_rich_text(entry.body, HELP_FEATURES)))
    with translation.override(language):  # link placeholders carry the page's language prefix
        return GuidePage(
            audience=audience,
            section=slug,
            title=help_files.render_placeholders(entry.title),
            html=help_files.render_placeholders(html),
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
    if audience != "customer" or not engine.guide_key_valid(guide_key):
        return ""
    return reverse("customer_help_section", args=[slug])
