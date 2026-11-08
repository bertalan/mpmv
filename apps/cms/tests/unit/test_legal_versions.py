"""CMS.5: which version of a legal page is in force, and when a publish records a new one.

Pure rules (no database): a version applies from its effective date until a later version
takes effect; a new text cannot take effect before the version it replaces.
"""

import datetime as dt

import pytest

from apps.cms.engine import VersionStamp, content_changed, is_backdated, version_in_force

V1 = VersionStamp(number=1, effective_date=dt.date(2027, 1, 1))
V2 = VersionStamp(number=2, effective_date=dt.date(2027, 6, 1))
V3 = VersionStamp(number=3, effective_date=dt.date(2027, 6, 1))  # a correction, same date


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (dt.date(2026, 12, 31), None),  # nothing in force yet
        (dt.date(2027, 1, 1), V1),  # from the effective date itself
        (dt.date(2027, 5, 31), V1),  # V2 is published but not in force yet
        (dt.date(2027, 6, 1), V2),
        (dt.date(2030, 1, 1), V2),
    ],
)
def test_the_version_in_force_is_the_latest_one_already_effective(
    today: dt.date, expected: VersionStamp | None
) -> None:
    assert version_in_force([V2, V1], today) == expected


def test_a_correction_with_the_same_date_replaces_the_earlier_text() -> None:
    assert version_in_force([V1, V2, V3], dt.date(2027, 7, 1)) == V3


def test_no_versions_means_nothing_in_force() -> None:
    assert version_in_force([], dt.date(2027, 1, 1)) is None


@pytest.mark.parametrize(
    ("latest", "new", "backdated"),
    [
        (None, dt.date(2020, 1, 1), False),  # the first version may have any date
        (dt.date(2027, 1, 1), dt.date(2027, 1, 1), False),
        (dt.date(2027, 1, 1), dt.date(2027, 2, 1), False),
        (dt.date(2027, 1, 1), dt.date(2026, 12, 31), True),
    ],
)
def test_a_new_version_cannot_take_effect_before_the_previous_one(
    latest: dt.date | None, new: dt.date, backdated: bool
) -> None:
    assert is_backdated(latest, new) is backdated


def test_content_changes_are_detected_on_title_date_and_text() -> None:
    current = {"title": "Terms", "effective_date": "2027-01-01", "body": [["paragraph", "a"]]}
    assert content_changed(None, current)
    assert not content_changed(dict(current), current)
    assert content_changed({**current, "title": "Terms of sale"}, current)
    assert content_changed({**current, "effective_date": "2027-02-01"}, current)
    assert content_changed({**current, "body": [["paragraph", "b"]]}, current)
