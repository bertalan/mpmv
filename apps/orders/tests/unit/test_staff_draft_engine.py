"""ORD.32: the pure rules of a staff draft's one-time link (no ORM)."""

import datetime as dt
import zoneinfo

from apps.orders import engine

ROME = zoneinfo.ZoneInfo("Europe/Rome")
NOW = dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.UTC)


def refusal(**kw: object) -> str | None:
    base: dict[str, object] = {
        "is_open": True,
        "issued_version": 3,
        "version": 3,
        "expires_at": NOW + dt.timedelta(days=1),
        "now": NOW,
    }
    return engine.draft_link_refusal(**{**base, **kw})  # type: ignore[arg-type]


def test_a_fresh_link_of_the_current_version_is_usable() -> None:
    assert refusal() is None


def test_a_used_link_is_refused() -> None:
    assert refusal(is_open=False) == engine.DRAFT_LINK_USED


def test_an_expired_link_is_refused_at_the_second_it_expires() -> None:
    assert refusal(expires_at=NOW) == engine.DRAFT_LINK_EXPIRED
    assert refusal(expires_at=None) == engine.DRAFT_LINK_EXPIRED


def test_a_link_of_an_older_version_is_refused() -> None:
    assert refusal(issued_version=2) == engine.DRAFT_LINK_CHANGED
    assert refusal(issued_version=None) == engine.DRAFT_LINK_CHANGED


def test_sends_are_counted_per_business_day() -> None:
    late_yesterday = dt.datetime(2026, 11, 17, 22, 30, tzinfo=dt.UTC)  # 23:30 in Rome
    just_after_midnight = dt.datetime(2026, 11, 17, 23, 30, tzinfo=dt.UTC)  # 00:30 in Rome
    times = [late_yesterday.isoformat(), just_after_midnight.isoformat(), NOW.isoformat()]
    assert engine.draft_sends_today(times, now=NOW, tz=ROME) == 2


def test_the_fourth_send_of_a_day_is_refused() -> None:
    times = [(NOW - dt.timedelta(hours=h)).isoformat() for h in (1, 2, 3)]
    assert engine.draft_sends_left(times, now=NOW, tz=ROME) == 0
    assert engine.draft_sends_left(times[:2], now=NOW, tz=ROME) == 1
    assert engine.DRAFT_MAX_SENDS_PER_DAY == 3


def test_the_token_digest_is_sha256_hex_and_never_the_token() -> None:
    digest = engine.draft_token_digest("abc")
    assert len(digest) == 64
    assert digest != "abc"
    assert digest == engine.draft_token_digest("abc")
