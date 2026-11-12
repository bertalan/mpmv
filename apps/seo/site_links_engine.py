"""Pure helpers of the temporary staff "Site links" page (OPS.12; remove before LIVE).

No ORM and no URL resolver here: `apps.seo.site_links` gathers the routes and the rows and
hands them over; these functions shape them into capped sections of links.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

LIMIT = 50  # links shown per list; the rest is counted ("and N more")


@dataclass(frozen=True)
class Link:
    label: str
    path: str
    depth: int = 0  # indentation level in a tree (CMS pages, categories, terms)


@dataclass(frozen=True)
class Section:
    title: str
    links: tuple[Link, ...]
    more: int = 0  # links left out by the cap
    note: str = ""


def section(
    title: str,
    links: Iterable[Link],
    *,
    limit: int = LIMIT,
    total: int | None = None,
    note: str = "",
) -> Section:
    """A section with the first `limit` links. `total` is the row count when the caller built
    links for the first rows only (the rest counts as "more")."""
    items = list(links)
    shown = tuple(items[:limit])
    more = len(items) - len(shown) if total is None else max(0, total - limit)
    return Section(title=title, links=shown, more=more, note=note)


def routed_links(
    candidates: Iterable[tuple[str, str]], reverse: Callable[[str], str | None]
) -> tuple[Link, ...]:
    """(label, URL name) pairs as links, in order; a name that does not reverse is left out."""
    links = []
    for label, name in candidates:
        path = reverse(name)
        if path is not None:
            links.append(Link(label=label, path=path))
    return tuple(links)


def tree_links(rows: Iterable[tuple[str, str | None, int]], *, base_depth: int) -> tuple[Link, ...]:
    """(label, path, tree depth) rows as indented links; rows without a path are left out."""
    return tuple(
        Link(label=label, path=path, depth=max(0, depth - base_depth))
        for label, path, depth in rows
        if path
    )
