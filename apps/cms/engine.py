"""Pure CMS rules (no ORM, no I/O).

Galleries (I18N.3, spec 09 §9.2): the replacement rule of D-18. An image is language neutral
unless it is dedicated to one language. A language with at least one dedicated image shows only
those; any other language shows the neutral images. Product galleries (CAT.4) follow the same
rule.

Legal pages (CMS.5, spec 10 §10.2): a legal page keeps one version history per language (Q-42):
each publish that changes the title, the text or the effective date adds a version. A version
applies from its effective date until a later version takes effect; a version published with
the same date as an earlier one is a correction and replaces it from that date.
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

NEUTRAL = ""  # the `language` of an image shown in every language without dedicated images


def gallery_for_language[T: Mapping[str, Any]](items: Iterable[T], language: str) -> list[T]:
    """The items to show in `language`, in their order. Each item has a `language` key."""
    wanted = language.lower()
    items = list(items)
    dedicated = [item for item in items if item["language"] and item["language"].lower() == wanted]
    if dedicated:
        return dedicated
    return [item for item in items if not item["language"]]


@dataclass(frozen=True)
class VersionStamp:
    number: int
    effective_date: dt.date


def version_in_force(stamps: Iterable[VersionStamp], today: dt.date) -> VersionStamp | None:
    """The version that applies on `today`: the highest-numbered one already effective."""
    effective = [stamp for stamp in stamps if stamp.effective_date <= today]
    return max(effective, key=lambda stamp: stamp.number, default=None)


def is_backdated(latest: dt.date | None, new: dt.date) -> bool:
    """True when a new version would take effect before the last recorded one."""
    return latest is not None and new < latest


def content_changed(latest: Mapping[str, Any] | None, current: Mapping[str, Any]) -> bool:
    """True when the page's published content differs from its last recorded version."""
    return latest is None or dict(latest) != dict(current)
