"""Validity periods of configuration rows (spec 01 "History"). Pure Python, no ORM.

A period is half-open, `[valid_from, valid_to)`; `valid_to = None` means open-ended. Rows are
never edited in place: a change is a new row, and the previous one gets its `valid_to`.
"""

import datetime as dt
from collections.abc import Iterable
from typing import Protocol


class Period(Protocol):
    @property
    def valid_from(self) -> dt.datetime: ...

    @property
    def valid_to(self) -> dt.datetime | None: ...


class NoValidRow(LookupError):
    """No configuration row covers the requested instant."""


class OverlappingValidity(LookupError):
    """More than one configuration row covers the requested instant."""


def covers(row: Period, at: dt.datetime) -> bool:
    return row.valid_from <= at and (row.valid_to is None or at < row.valid_to)


def pick_valid[P: Period](rows: Iterable[P], at: dt.datetime, *, what: str) -> P:
    """The single row valid at `at`; a clear error if there is none or more than one."""
    matches = [row for row in rows if covers(row, at)]
    if not matches:
        raise NoValidRow(f"No {what} is valid at {at.isoformat()}; configure one first.")
    if len(matches) > 1:
        raise OverlappingValidity(
            f"{len(matches)} {what} rows are valid at {at.isoformat()}; "
            "validity periods must not overlap."
        )
    return matches[0]


def next_start_error(valid_from: dt.datetime, latest: Period | None) -> str | None:
    """Why a new row starting at `valid_from` cannot follow `latest`; None when it can.

    History only grows forward: the new row starts after the latest one, and only an open-ended
    latest row is closed (an already closed row is never shortened).
    """
    if latest is None:
        return None
    if valid_from <= latest.valid_from:
        return f"Must be after {latest.valid_from.isoformat()}, the latest start."
    if latest.valid_to is not None and latest.valid_to > valid_from:
        return f"Must not be before the latest end, {latest.valid_to.isoformat()}."
    return None
