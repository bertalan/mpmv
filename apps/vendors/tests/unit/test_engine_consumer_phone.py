"""CMP.41 follow-up (owner decision 2026-11-19): a seller who sells to consumers must give a phone
number; it is stored normalised and checked for its format. Fictional numbers only: the UK drama
range +44 20 7946 0xxx (Ofcom)."""

import pytest

from apps.vendors import engine

NUMBER = "+442079460123"


@pytest.mark.parametrize(
    "typed",
    ["+44 20 7946 0123", "+44 (20) 7946-0123", "0044 20 7946.0123", " +44/20/79460123 "],
)
def test_a_phone_is_stored_without_separators_and_with_a_plus_for_00(typed: str) -> None:
    assert engine.normalized_phone(typed) == NUMBER


@pytest.mark.parametrize("typed", ["+44 20 7946 0123", "020 7946 0123", ""])
def test_a_plausible_or_empty_phone_has_no_error(typed: str) -> None:
    identity = engine.LegalIdentity(country="GB", phone=typed)
    assert "phone" not in engine.identity_errors(identity)


@pytest.mark.parametrize("typed", ["call me", "+44 20", "+44 20 7946 0123 ext 5", "1" * 16])
def test_an_implausible_phone_is_an_error(typed: str) -> None:
    identity = engine.LegalIdentity(country="GB", phone=typed)
    assert "phone" in engine.identity_errors(identity)


def test_normalising_the_identity_compacts_the_phone() -> None:
    identity = engine.LegalIdentity(country="GB", phone="+44 20 7946 0123")
    assert engine.normalized(identity).phone == NUMBER


@pytest.mark.parametrize(
    ("phone", "consumers", "missing"),
    [("", True, True), ("  ", True, True), (NUMBER, True, False), ("", False, False)],
)
def test_the_phone_is_missing_only_for_a_seller_to_consumers(
    phone: str, consumers: bool, missing: bool
) -> None:
    assert engine.consumer_phone_missing(phone, sells_to_consumers=consumers) is missing


def test_activation_asks_for_the_phone_of_a_seller_to_consumers_without_any_rule() -> None:
    values = {"country": "US", "phone": ""}
    assert engine.platform_duties_missing(values, [], certified=True, phone_required=True) == (
        "phone",
    )
    assert engine.platform_duties_missing(values, [], certified=True) == ()


def test_a_phone_a_rule_already_asks_for_is_named_once() -> None:
    rules = [engine.Requirement(field="phone", region=engine.EU, purpose=engine.ACTIVATION)]
    values = {"country": "DE", "phone": ""}
    assert engine.platform_duties_missing(values, rules, certified=False, phone_required=True) == (
        "phone",
        engine.SELF_CERTIFICATION,
    )
