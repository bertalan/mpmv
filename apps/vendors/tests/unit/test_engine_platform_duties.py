"""CMP.25: the pure activation gate for platform duties (DSA article 30 trader data and the
article 30(1)(e) self-certification)."""

import pytest

from apps.vendors import engine

ACTIVATION_RULES = [
    engine.Requirement(field="phone", region=engine.EU, purpose=engine.ACTIVATION),
    engine.Requirement(field="registered_city", region=engine.EU, purpose=engine.ACTIVATION),
    engine.Requirement(
        field="business_registration_number",
        region=engine.EU,
        legal_form=engine.SRL,
        purpose=engine.ACTIVATION,
    ),
    engine.Requirement(field="iban", region=engine.EU, purpose=engine.PAYOUT),
]


def values(**fields: str) -> dict[str, str]:
    return {"country": "DE", "legal_form": engine.SRL, **fields}


def complete() -> dict[str, str]:
    return values(
        phone="+49 30 1234", registered_city="Berlin", business_registration_number="HRB1"
    )


def test_a_complete_vendor_has_nothing_missing() -> None:
    assert engine.platform_duties_missing(complete(), ACTIVATION_RULES, certified=True) == ()


def test_missing_telephone_and_trade_register_entry_are_named() -> None:
    result = engine.platform_duties_missing(
        values(registered_city="Berlin"), ACTIVATION_RULES, certified=True
    )
    assert result == ("business_registration_number", "phone")


def test_trade_register_entry_is_only_required_where_registered() -> None:
    sole = values(
        legal_form=engine.SOLE_PROPRIETORSHIP, phone="+49 30 1234", registered_city="Berlin"
    )
    assert engine.platform_duties_missing(sole, ACTIVATION_RULES, certified=True) == ()


def test_a_missing_self_certification_is_named_last() -> None:
    assert engine.platform_duties_missing(complete(), ACTIVATION_RULES, certified=False) == (
        engine.SELF_CERTIFICATION,
    )


def test_payout_rules_are_not_activation_rules() -> None:
    result = engine.platform_duties_missing(values(), ACTIVATION_RULES, certified=True)
    assert "iban" not in result


def test_phone_is_a_field_a_requirement_can_name() -> None:
    assert "phone" in engine.REQUIREMENT_FIELDS
    assert engine.REQUIREMENT_FIELDS[: len(engine.LEGAL_FIELDS)] == engine.LEGAL_FIELDS


def test_an_unknown_field_is_refused() -> None:
    rules = [engine.Requirement(field="nope", purpose=engine.ACTIVATION)]
    with pytest.raises(engine.UnknownLegalField):
        engine.platform_duties_missing(values(), rules, certified=True)


def test_the_checklist_blocks_on_platform_duties_but_not_on_dac7() -> None:
    checklist = engine.onboarding_checklist(
        contract_version=1,
        current_contract_version=1,
        dac7_missing=["iban"],
        payout=engine.Completeness(missing=("iban",)),
        platform_duties_missing=("phone", engine.SELF_CERTIFICATION),
    )
    assert checklist.blocking_missing == (engine.PLATFORM_DUTIES,)
    with pytest.raises(engine.ActivationNotAllowed, match="phone, self_certification"):
        engine.check_activation(checklist)


def test_self_certification_text_has_a_stable_hash_per_language() -> None:
    assert engine.self_certification_hash("en") == engine.self_certification_hash("en")
    assert engine.self_certification_hash("en") != engine.self_certification_hash("it")
    assert engine.SELF_CERTIFICATION_VERSION >= 1
    with pytest.raises(engine.SelfCertificationError):
        engine.self_certification_text("xx")


def test_the_hash_covers_the_language_code(monkeypatch: pytest.MonkeyPatch) -> None:
    same = {"en": "Same text.", "it": "Same text."}
    monkeypatch.setattr(engine, "SELF_CERTIFICATION_TEXTS", same)
    assert engine.self_certification_hash("en") != engine.self_certification_hash("it")
