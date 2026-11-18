"""Page revision retention, the pure part (CMS.18): no Django, no models, no I/O.

The service (`retention`) reads revisions and hands them over as plain data; this module decides
which of them may be deleted. A revision is kept when any of these holds:

- it is the page's live or latest revision, or has a scheduled publication;
- it is one of the object's `keep_last` newest revisions;
- it is not older than `keep_months` calendar months.
"""

import calendar
import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

KEEP_MONTHS = 12
KEEP_LAST = 10


@dataclass(frozen=True)
class RevisionInfo:
    pk: int
    created_at: dt.datetime
    is_live: bool = False
    is_latest: bool = False
    is_scheduled: bool = False  # `approved_go_live_at` is set

    @property
    def is_protected(self) -> bool:
        return self.is_live or self.is_latest or self.is_scheduled


def months_back(moment: dt.datetime, months: int) -> dt.datetime:
    """`moment` minus `months` calendar months; the day is clamped to the target month's end."""
    index = moment.year * 12 + moment.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def revisions_to_delete(
    revisions_per_object: Mapping[str, Sequence[RevisionInfo]],
    now: dt.datetime,
    keep_months: int = KEEP_MONTHS,
    keep_last: int = KEEP_LAST,
) -> dict[str, list[int]]:
    """The revision ids to delete per object (newest first); objects with none are absent.

    The `keep_last` newest are ranked by (created_at, id), so equal timestamps stay stable.
    A revision exactly `keep_months` old is kept.
    """
    cutoff = months_back(now, keep_months)
    result: dict[str, list[int]] = {}
    for key, revisions in revisions_per_object.items():
        newest_first = sorted(revisions, key=lambda r: (r.created_at, r.pk), reverse=True)
        doomed = [
            r.pk for r in newest_first[keep_last:] if r.created_at < cutoff and not r.is_protected
        ]
        if doomed:
            result[key] = doomed
    return result
