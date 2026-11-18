"""CMS.18: the pure revision retention rule (no database)."""

import datetime as dt

from apps.cms import retention_engine as engine
from apps.cms.retention_engine import RevisionInfo

NOW = dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)


def rev(pk: int, created: dt.datetime, **flags: bool) -> RevisionInfo:
    return RevisionInfo(pk, created, **flags)


def old(pk: int) -> RevisionInfo:
    return rev(pk, NOW - dt.timedelta(days=700 - pk))


def test_months_back_clamps_the_day() -> None:
    assert engine.months_back(dt.datetime(2026, 3, 31, tzinfo=dt.UTC), 1) == dt.datetime(
        2026, 2, 28, tzinfo=dt.UTC
    )
    assert engine.months_back(NOW, 12) == dt.datetime(2025, 11, 18, 12, tzinfo=dt.UTC)
    assert engine.months_back(dt.datetime(2026, 2, 3, tzinfo=dt.UTC), 3) == dt.datetime(
        2025, 11, 3, tzinfo=dt.UTC
    )


def test_old_revisions_beyond_the_latest_ten_go() -> None:
    revisions = [old(pk) for pk in range(1, 16)]  # 15 old ones, ids grow with time
    assert engine.revisions_to_delete({"p": revisions}, NOW) == {"p": [5, 4, 3, 2, 1]}


def test_exactly_the_tenth_latest_is_kept() -> None:
    revisions = [old(pk) for pk in range(1, 11)]
    assert engine.revisions_to_delete({"p": revisions}, NOW) == {}
    revisions.append(old(11))
    assert engine.revisions_to_delete({"p": revisions}, NOW) == {"p": [1]}


def test_exactly_twelve_months_old_is_kept_and_a_second_older_goes() -> None:
    boundary = engine.months_back(NOW, 12)
    newer = [rev(100 + n, NOW - dt.timedelta(days=n)) for n in range(10)]
    on = rev(1, boundary)
    before = rev(2, boundary - dt.timedelta(seconds=1))
    assert engine.revisions_to_delete({"p": [*newer, on, before]}, NOW) == {"p": [2]}


def test_recent_revisions_stay_whatever_their_number() -> None:
    revisions = [rev(n, NOW - dt.timedelta(days=n)) for n in range(1, 40)]
    assert engine.revisions_to_delete({"p": revisions}, NOW) == {}


def test_live_latest_and_scheduled_revisions_are_never_deleted() -> None:
    revisions = [old(pk) for pk in range(20, 40)]
    revisions += [
        rev(1, NOW - dt.timedelta(days=900), is_live=True),
        rev(2, NOW - dt.timedelta(days=900), is_latest=True),
        rev(3, NOW - dt.timedelta(days=900), is_scheduled=True),
        rev(4, NOW - dt.timedelta(days=900)),
    ]
    deleted = engine.revisions_to_delete({"p": revisions}, NOW)["p"]
    assert not {1, 2, 3} & set(deleted)
    assert 4 in deleted


def test_each_object_is_ranked_on_its_own() -> None:
    result = engine.revisions_to_delete(
        {"a": [old(pk) for pk in range(1, 12)], "b": [old(pk) for pk in range(50, 55)]}, NOW
    )
    assert result == {"a": [1]}


def test_custom_limits() -> None:
    revisions = [old(pk) for pk in range(1, 6)]
    assert engine.revisions_to_delete({"p": revisions}, NOW, keep_last=2) == {"p": [3, 2, 1]}
