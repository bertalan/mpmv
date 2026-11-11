"""The help lookup (ARC.32): which entry answers a screen, per language, cached.

`help_for(route_name, page_type, language)` tries the route name, then the page type, then the
general entry (`engine.pick_help_row`), in the language and else in the primary one. The answer,
or its absence, is cached per (language, route, page type) under a generation number that an
entry's save or delete bumps, so a change shows at once on every worker sharing the cache. With
no entry at all the answer is None: callers show a polite empty message, never an error.
"""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from wagtail.rich_text import expand_db_html

from . import engine
from .models import HelpEntry

GENERATION_KEY = "cms:help:generation"
TIMEOUT = 3600
_MISSING = "-"  # a cached "no entry": the cache cannot tell None from a miss


@dataclass(frozen=True)
class HelpContent:
    title: str
    html: str
    vendor_guide: bool
    source: str  # "route", "page_type" or "general"


def _generation() -> int:
    return int(cache.get_or_set(GENERATION_KEY, 1, None) or 1)


def drop_cache(**kwargs: Any) -> None:
    """`post_save` and `post_delete` of an entry: start a new generation of cached answers."""
    cache.set(GENERATION_KEY, _generation() + 1, None)


def _rows(candidates: list[tuple[str, str]], languages: set[str]) -> list[tuple[Any, HelpEntry]]:
    query = Q()
    for kind, key in candidates:
        query |= Q(kind=kind, key=key)
    entries = HelpEntry.objects.filter(query, locale__language_code__in=languages).select_related(
        "locale"
    )
    return [
        (
            engine.HelpRow(
                kind=entry.kind,
                key=entry.key,
                language=entry.locale.language_code,
                title=entry.title,
                body=entry.body,
                vendor_guide=entry.show_vendor_guide,
            ),
            entry,
        )
        for entry in entries
    ]


def help_for(route_name: str, page_type: str, language: str) -> HelpContent | None:
    """The help that answers a screen in `language`, or None when nothing is configured."""
    candidates = engine.help_candidates(route_name, page_type)
    primary = settings.LANGUAGE_CODE
    wanted = ",".join(f"{kind}={value}" for kind, value in candidates)
    key = f"cms:help:{_generation()}:{language}:{wanted}"
    cached = cache.get(key)
    if cached is not None:
        return None if cached == _MISSING else cached
    pairs = _rows(candidates, {language, primary})
    row = engine.pick_help_row((r for r, _e in pairs), candidates, language, primary)
    content = (
        None
        if row is None
        else HelpContent(
            title=row.title,
            html=str(expand_db_html(row.body)),
            vendor_guide=row.vendor_guide,
            source=row.kind,
        )
    )
    cache.set(key, _MISSING if content is None else content, TIMEOUT)
    return content
