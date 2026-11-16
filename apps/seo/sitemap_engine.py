"""Per-language sitemaps (I18N.6, spec 09 §9.2), pure parts: which page a language lists, and
the XML. No Django, no ORM.

A language's sitemap lists only the pages that have their own published text in it. A fallback
copy (the primary-language text shown under another language's prefix) is never listed: its
canonical is the primary-language page, which that language's sitemap already lists.
"""

import datetime as dt
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from xml.sax.saxutils import escape

MAX_URLS = 50_000  # the sitemaps.org limits of one file
MAX_BYTES = 50 * 1024 * 1024
NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n'


@dataclass(frozen=True)
class Entry:
    loc: str  # absolute URL
    lastmod: dt.date | None = None


def sitemap_path(paths: Mapping[str, str], published: Collection[str], language: str) -> str | None:
    """The path `language`'s sitemap lists for an object: its own, when the object has a
    published text in `language`; None for a fallback copy or a language without a page."""
    if language not in published:
        return None
    return paths.get(language)


def _row(entry: Entry) -> str:
    lastmod = f"<lastmod>{entry.lastmod.isoformat()}</lastmod>" if entry.lastmod else ""
    return f"<url><loc>{escape(entry.loc)}</loc>{lastmod}</url>"


def chunk_entries(entries: Iterable[Entry]) -> list[list[Entry]]:
    """Split into files that respect `MAX_URLS` and `MAX_BYTES` (read at call time). At least
    one (possibly empty) chunk, so a language always has a first file."""
    chunks: list[list[Entry]] = [[]]
    size = len(_HEAD) + 200  # the urlset wrapper
    for entry in entries:
        row = len(_row(entry).encode())
        if chunks[-1] and (len(chunks[-1]) >= MAX_URLS or size + row > MAX_BYTES):
            chunks.append([])
            size = len(_HEAD) + 200
        chunks[-1].append(entry)
        size += row
    return chunks


def urlset_xml(entries: Iterable[Entry]) -> str:
    rows = "".join(_row(entry) for entry in entries)
    return f'{_HEAD}<urlset xmlns="{NAMESPACE}">{rows}</urlset>\n'


def index_xml(locations: Iterable[str]) -> str:
    rows = "".join(f"<sitemap><loc>{escape(loc)}</loc></sitemap>" for loc in locations)
    return f'{_HEAD}<sitemapindex xmlns="{NAMESPACE}">{rows}</sitemapindex>\n'
