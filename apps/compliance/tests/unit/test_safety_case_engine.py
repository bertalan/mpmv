"""CMP.3: GPSR article 22 deadlines as pure functions (spec 11 CMP.3).

An online marketplace acts on an authority order within 2 working days and handles a product
safety notice within 3 working days. The day of receipt is day 0; the deadline is the end of the
n-th working day after it. Working days are Monday to Friday; the public holidays passed in are
skipped too (none by default, which gives the earliest, safest deadline).
"""

import datetime as dt

import pytest

from apps.compliance import engine

MONDAY = dt.date(2026, 11, 9)
FRIDAY = dt.date(2026, 11, 13)
SATURDAY = dt.date(2026, 11, 14)


@pytest.mark.parametrize(
    ("start", "days", "expected"),
    [
        (MONDAY, 2, dt.date(2026, 11, 11)),  # Wednesday
        (MONDAY, 3, dt.date(2026, 11, 12)),  # Thursday
        (FRIDAY, 2, dt.date(2026, 11, 17)),  # Tuesday: the weekend does not count
        (FRIDAY, 3, dt.date(2026, 11, 18)),
        (SATURDAY, 2, dt.date(2026, 11, 17)),  # received on a weekend: Monday is day 1
        (MONDAY, 0, MONDAY),
    ],
)
def test_add_working_days(start: dt.date, days: int, expected: dt.date) -> None:
    assert engine.add_working_days(start, days) == expected


def test_holidays_are_skipped() -> None:
    holiday = dt.date(2026, 11, 10)
    assert engine.add_working_days(MONDAY, 2, holidays=frozenset({holiday})) == dt.date(
        2026, 11, 12
    )


def test_negative_days_are_refused() -> None:
    with pytest.raises(ValueError, match="days"):
        engine.add_working_days(MONDAY, -1)


def test_deadline_per_kind() -> None:
    assert engine.safety_case_due_on(engine.AUTHORITY_ORDER, FRIDAY) == dt.date(2026, 11, 17)
    assert engine.safety_case_due_on(engine.SAFETY_NOTICE, FRIDAY) == dt.date(2026, 11, 18)
    with pytest.raises(ValueError, match="kind"):
        engine.safety_case_due_on("other", FRIDAY)


def test_overdue_only_after_the_due_day_and_while_open() -> None:
    due = dt.date(2026, 11, 11)
    assert not engine.is_overdue(due, today=due, closed=False)
    assert engine.is_overdue(due, today=dt.date(2026, 11, 12), closed=False)
    assert not engine.is_overdue(due, today=dt.date(2026, 11, 12), closed=True)


def test_working_days_left() -> None:
    due = dt.date(2026, 11, 17)  # Tuesday
    assert engine.working_days_left(due, today=FRIDAY) == 2
    assert engine.working_days_left(due, today=due) == 0
    assert engine.working_days_left(due, today=dt.date(2026, 11, 19)) == -2
