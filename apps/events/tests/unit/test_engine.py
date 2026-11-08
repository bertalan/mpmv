"""EVT.1: the pure rules behind the event page's denormalised status and its RRULE check."""

import pytest

from apps.events import engine


@pytest.mark.parametrize(
    ("live", "in_workflow", "cancelled", "hidden", "expected"),
    [
        (False, False, False, False, engine.DRAFT),  # never published, no workflow
        (False, True, False, False, engine.PENDING),  # first publication in review
        (True, False, False, False, engine.PUBLISHED),
        (True, True, False, False, engine.PUBLISHED),  # an edit of a live page in review
        (True, False, True, False, engine.CANCELLED),  # stays online, marked cancelled
        (False, False, False, True, engine.HIDDEN),  # unpublished by staff
        (False, False, True, True, engine.HIDDEN),  # hiding wins over cancelling
    ],
)
def test_event_status_follows_the_wagtail_state(
    live: bool, in_workflow: bool, cancelled: bool, hidden: bool, expected: str
) -> None:
    status = engine.event_status(
        live=live, in_workflow=in_workflow, cancelled=cancelled, hidden=hidden
    )
    assert status == expected


def test_the_status_values_are_the_spec_values() -> None:
    assert engine.STATUSES == ("draft", "pending", "published", "cancelled", "hidden")


def test_event_types_have_no_closure_type() -> None:
    # Owner decision Q-73: a vendor's closure is a vendor setting (VEN.20), not an event.
    assert engine.EVENT_TYPES == ("promotion", "fair", "other")
    assert "closure" not in engine.EVENT_TYPES


@pytest.mark.parametrize(
    ("rrule", "expected"),
    [
        ("", False),
        ("FREQ=YEARLY;COUNT=3", False),
        ("FREQ=WEEKLY;UNTIL=20271231T000000Z", False),
        ("FREQ=WEEKLY;COUNT=3;UNTIL=20271231T000000Z", True),
        ("freq=weekly;until=20271231;count=2", True),
        ("FREQ=WEEKLY;BYDAY=MO", False),
    ],
)
def test_count_and_until_together_are_detected(rrule: str, expected: bool) -> None:
    assert engine.has_count_and_until(rrule) is expected
