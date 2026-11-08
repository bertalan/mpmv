"""VEN.24: pure rules of claiming an imported vendor (identity check, claim statuses)."""

import pytest

from apps.vendors import engine

VAT = "IT01234567897"
TIN = "RSSMRA80A01H501U"


@pytest.mark.parametrize(
    "typed",
    [
        VAT,
        "01234567897",  # without the country prefix
        "it 0123 4567 897",
        "IT-01234567897",
        TIN,
        TIN.lower(),
    ],
)
def test_the_stored_vat_number_or_tin_is_recognised(typed: str) -> None:
    assert engine.claim_identity_matches(typed, vat_number=VAT, tax_code=TIN, country="IT")


def test_a_vat_number_stored_without_prefix_matches_one_typed_with_it() -> None:
    assert engine.claim_identity_matches(
        "IT01234567897", vat_number="01234567897", tax_code="", country="IT"
    )


@pytest.mark.parametrize(
    "typed", ["", "   ", "IT", "01234567890", "IT0123456789", "RSSMRA80A01H501X"]
)
def test_anything_else_is_refused(typed: str) -> None:
    assert not engine.claim_identity_matches(typed, vat_number=VAT, tax_code=TIN, country="IT")


def test_empty_stored_values_never_match() -> None:
    assert not engine.claim_identity_matches("", vat_number="", tax_code="", country="IT")
    assert not engine.claim_identity_matches("IT", vat_number="", tax_code="", country="IT")


def test_a_tin_with_punctuation_matches() -> None:
    assert engine.claim_identity_matches(
        "07-0000123", vat_number="", tax_code="07-0000123", country="US"
    )
    assert engine.claim_identity_matches(
        "070000123", vat_number="", tax_code="07-0000123", country="US"
    )


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        (engine.CLAIM_OPEN, True),
        (engine.CLAIM_LOCKED, True),
        (engine.CLAIM_REJECTED, True),
        (engine.CLAIM_SUBMITTED, False),
        (engine.CLAIM_APPROVED, False),
    ],
)
def test_a_link_can_be_regenerated_only_while_the_claim_is_not_pending_or_done(
    status: str, allowed: bool
) -> None:
    if allowed:
        engine.check_claim_link_can_be_sent(status)
    else:
        with pytest.raises(engine.ClaimError):
            engine.check_claim_link_can_be_sent(status)


def test_the_claim_locks_after_the_maximum_number_of_failures() -> None:
    assert not engine.claim_locks_after(4, max_failures=5)
    assert engine.claim_locks_after(5, max_failures=5)
