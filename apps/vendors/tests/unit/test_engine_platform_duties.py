"""CMP.25: the pure activation gate for platform duties (DSA article 30 trader data and the
article 30(1)(e) self-certification)."""

import pytest

from apps.core.i18n import configured_languages
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


def test_every_configured_language_has_a_self_certification_text() -> None:
    # I18N.20: a language cannot open to vendors without its own text.
    assert engine.self_certification_languages_missing(configured_languages()) == ()
    for code in configured_languages():
        assert engine.self_certification_text(code).strip()
    texts = [engine.SELF_CERTIFICATION_TEXTS[code] for code in configured_languages()]
    assert len(set(texts)) == len(texts)  # no language reuses the text of another


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_a_missing_or_empty_self_certification_text_is_reported(
    monkeypatch: pytest.MonkeyPatch, empty: str | None
) -> None:
    codes = configured_languages()
    texts = dict(engine.SELF_CERTIFICATION_TEXTS)
    if empty is None:
        del texts[codes[-1]]
    else:
        texts[codes[-1]] = empty
    monkeypatch.setattr(engine, "SELF_CERTIFICATION_TEXTS", texts)
    assert engine.self_certification_languages_missing(codes) == (codes[-1],)
    with pytest.raises(engine.SelfCertificationError):
        engine.self_certification_text(codes[-1])


def test_the_self_certification_texts_cover_the_go_live_languages() -> None:
    # D-18: en, it, de, fr, es at go-live, whatever MPMV_LANGUAGES this run uses.
    assert engine.self_certification_languages_missing(("en", "it", "de", "fr", "es")) == ()


def test_the_hash_covers_the_language_code(monkeypatch: pytest.MonkeyPatch) -> None:
    same = {"en": "Same text.", "it": "Same text."}
    monkeypatch.setattr(engine, "SELF_CERTIFICATION_TEXTS", same)
    assert engine.self_certification_hash("en") != engine.self_certification_hash("it")


# The first 12 hex digits of `self_certification_hash` per language, for every version ever
# released. Acceptances store the version and the full hash, so a version's row is frozen once
# an acceptance of that language may exist (LIVE, STAGE or any other installation).
SELF_CERTIFICATION_DIGESTS = {
    1: {
        "en": "09f0267f24b6",
        "it": "db15f588e748",
        "de": "bb6c7fe50d8c",
        "fr": "5719ddb7afa2",
        "es": "5b0a9df03a6e",
    },
}


def test_a_changed_self_certification_text_needs_a_version_bump() -> None:
    version = engine.SELF_CERTIFICATION_VERSION
    assert version == max(SELF_CERTIFICATION_DIGESTS), (
        "SELF_CERTIFICATION_VERSION changed: add a row for it to SELF_CERTIFICATION_DIGESTS "
        "and keep the rows of the earlier versions as they are."
    )
    current = {
        code: engine.self_certification_hash(code)[:12] for code in engine.SELF_CERTIFICATION_TEXTS
    }
    assert current == SELF_CERTIFICATION_DIGESTS[version], (
        "A self-certification text changed (or a language was added) without a bump of "
        "SELF_CERTIFICATION_VERSION. Bump the version and add a row. Edit the current row in "
        "place only for a language in which no acceptance can exist yet."
    )


def test_unreviewed_languages_are_those_with_a_text_and_no_review_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "SELF_CERTIFICATION_TEXTS", {"en": "A.", "it": "B.", "de": "C."})
    monkeypatch.setattr(engine, "SELF_CERTIFICATION_REVIEWED_LANGUAGES", frozenset({"en", "it"}))
    # "xx" has no text: `self_certification_languages_missing` reports it, not this function.
    assert engine.self_certification_languages_unreviewed(("en", "it", "de", "xx")) == ("de",)
    assert engine.self_certification_languages_missing(("en", "it", "de", "xx")) == ("xx",)
