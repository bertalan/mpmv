"""Orphan image cleanup, the pure part (CAT.25): no Django, no models, no I/O.

The service (`media_cleanup`) gathers which images are referenced; this module decides which
are orphans and which are old enough to be offered for deletion.
"""

import datetime as dt
from collections.abc import Collection, Iterable

# A fresh upload may still be waiting to be attached (a vendor uploads an event image, then
# saves the form): an image younger than this is never listed or deleted.
GRACE = dt.timedelta(hours=24)


def orphan_ids(candidates: Iterable[int], referenced: Collection[int]) -> list[int]:
    """The candidates nothing references, in the order given."""
    used = set(referenced)
    return [pk for pk in candidates if pk not in used]


def is_past_grace(created_at: dt.datetime, now: dt.datetime) -> bool:
    return now - created_at >= GRACE


def source_label(model_label: str, field: str) -> str:
    """How a reference is named in reports: `<app>.<model>.<field>`."""
    return f"{model_label}.{field}"
