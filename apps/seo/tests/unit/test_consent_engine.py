"""SEO.5 and SEO.9: the consent rules are pure and hostile-input safe."""

import pytest

from apps.seo import engine

ID = "a" * 32


def decided(*granted: str) -> engine.ConsentState:
    return engine.ConsentState(True, frozenset(granted), engine.BANNER_VERSION, ID)


def test_nothing_gated_is_allowed_before_a_choice() -> None:
    for category in engine.GATED_CATEGORIES:
        assert not engine.UNDECIDED.allows(category)


def test_necessary_is_always_allowed() -> None:
    assert engine.UNDECIDED.allows(engine.NECESSARY)
    assert decided().allows(engine.NECESSARY)


def test_a_category_is_allowed_only_when_granted() -> None:
    state = decided(engine.STATISTICS)
    assert state.allows(engine.STATISTICS)
    assert not state.allows(engine.MARKETING)
    assert not state.allows("unknown")


def test_accept_all_grants_every_gated_category_and_reject_all_none() -> None:
    assert engine.granted_for(engine.ACCEPT_ALL) == frozenset(engine.GATED_CATEGORIES)
    assert engine.granted_for(engine.REJECT_ALL, engine.GATED_CATEGORIES) == frozenset()
    assert engine.granted_for("nonsense", engine.GATED_CATEGORIES) == frozenset()


def test_a_custom_choice_keeps_only_known_gated_categories() -> None:
    granted = engine.granted_for(
        engine.CUSTOM, ["statistics", "necessary", "<script>", "marketing"]
    )
    assert granted == frozenset({"statistics", "marketing"})


def test_the_cookie_round_trips_in_a_fixed_order() -> None:
    state = decided(engine.MARKETING, engine.PREFERENCES)
    value = engine.encode_cookie(state)
    assert value == f"v1.{engine.BANNER_VERSION}.{ID}.preferences-marketing"
    assert engine.parse_cookie(value) == state


def test_a_reject_all_cookie_has_no_categories() -> None:
    value = engine.encode_cookie(decided())
    assert value.endswith(f".{ID}.")
    assert engine.parse_cookie(value) == decided()


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "garbage",
        "v2.1." + ID + ".marketing",
        "v1.1.short.marketing",
        "v1.1." + ID.upper() + ".",
        "v1.1." + ID + ".marketing;x",
        "v1.." + ID + ".",
        "v1.1." + ID + "." + "a" * 100,
    ],
)
def test_a_hostile_or_malformed_cookie_is_not_decided(value: str | None) -> None:
    assert not engine.parse_cookie(value).decided


def test_unknown_categories_in_a_cookie_are_dropped() -> None:
    state = engine.parse_cookie(f"v1.{engine.BANNER_VERSION}.{ID}.marketing-bogus-necessary")
    assert state.granted == frozenset({engine.MARKETING})


def test_another_banner_version_asks_again_and_keeps_the_id() -> None:
    state = engine.parse_cookie(f"v1.0.{ID}.marketing")
    assert not state.decided
    assert state.granted == frozenset()
    assert state.consent_id == ID


def test_signals_are_denied_until_granted_except_security_storage() -> None:
    signals = engine.consent_signals(engine.UNDECIDED)
    assert signals["security_storage"] == engine.GRANTED
    assert {v for k, v in signals.items() if k != "security_storage"} == {engine.DENIED}


def test_signals_follow_the_granted_categories() -> None:
    signals = engine.consent_signals(decided(engine.STATISTICS, engine.PREFERENCES))
    assert signals["analytics_storage"] == engine.GRANTED
    assert signals["functionality_storage"] == engine.GRANTED
    assert signals["personalization_storage"] == engine.GRANTED
    assert signals["ad_storage"] == engine.DENIED
    assert signals["ad_user_data"] == engine.DENIED
    assert signals["ad_personalization"] == engine.DENIED


def test_every_gated_category_drives_a_signal() -> None:
    assert set(engine.SIGNAL_CATEGORY.values()) == set(engine.ALL_CATEGORIES)
