"""ING.5: schema validation, policy and the fake enricher (pure, no database)."""

import pytest

from apps.ingest import enrich_engine as eng
from apps.ingest.enrichment import (
    FakeEnricher,
    ProductFacts,
    ProviderError,
    TextEnricher,
    TextPolicy,
)

POLICY = TextPolicy()
FACTS = ProductFacts(sku="SKU-1", title="Ceramic mug", source_text="Holds 300 ml.")


def good(**changes: object) -> dict[str, object]:
    raw = FakeEnricher(changes).describe(FACTS, "en", POLICY).raw
    return dict(raw)


def test_fake_is_a_text_enricher() -> None:
    assert isinstance(FakeEnricher(), TextEnricher)


def test_valid_output_passes_schema_and_policy() -> None:
    text, errors = eng.validate_schema(good(), "en")
    assert errors == []
    assert text is not None
    assert eng.check_policy(text, POLICY) == []


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({**good(), "extra": "x"}, "unknown field"),
        ({**good(), "title": 5}, "expected text"),
        ({**good(), "description": None}, "expected text"),
    ],
)
def test_schema_rejects_bad_shape(raw: dict[str, object], fragment: str) -> None:
    text, errors = eng.validate_schema(raw, "en")
    assert text is None
    assert any(fragment in e for e in errors)


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"title": ""}, "title: required"),
        ({"seo_title": "x" * 71}, "seo_title: longer"),
        ({"description": "See <b>this</b>"}, "contains HTML"),
        ({"description": "Visit https://example.com now"}, "contains a URL"),
        ({"description": "An eco-friendly mug"}, "environmental claim"),
        ({"description": "Fits all models"}, "fitment claim"),
        ({"description": "Lorem ipsum dolor"}, "placeholder"),
    ],
)
def test_out_of_policy_texts_are_flagged(changes: dict[str, object], fragment: str) -> None:
    text, errors = eng.validate_schema(good(**changes), "en")
    assert errors == []
    assert text is not None
    assert any(fragment in v for v in eng.check_policy(text, POLICY))


def test_banned_terms_come_from_the_policy() -> None:
    policy = TextPolicy(banned_terms=("Mug",))
    text, _ = eng.validate_schema(good(), "en")
    assert text is not None
    assert any("banned term" in v for v in eng.check_policy(text, policy))


def test_language_outside_policy_is_flagged() -> None:
    text, _ = eng.validate_schema(good(), "it")
    assert text is not None
    assert "language not allowed: it" in eng.check_policy(text, POLICY)


def test_only_english_and_never_over_existing_texts() -> None:
    assert eng.can_write_language("en", POLICY, {})
    assert not eng.can_write_language("it", POLICY, {})
    # A migrated Italian text is never overwritten, and an existing EN text is not either.
    assert not eng.can_write_language("it", POLICY, {"it": {"title": "Tazza"}})
    assert not eng.can_write_language("en", POLICY, {"en": {"title": "Mug"}})


def test_fake_records_calls_and_can_fail() -> None:
    fake = FakeEnricher(error="boom")
    with pytest.raises(ProviderError):
        fake.describe(FACTS, "en", POLICY)
    assert fake.calls == [(FACTS, "en")]
