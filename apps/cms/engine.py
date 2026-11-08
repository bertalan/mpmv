"""Pure rules for versioned legal pages (CMS.5, spec 10 §10.2). No ORM, no I/O.

A legal page keeps one version history per language (Q-42): each publish that changes the
title, the text or the effective date adds a version. A version applies from its effective date
until a later version takes effect; a version published with the same date as an earlier one is
a correction and replaces it from that date.
"""

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


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
