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
        ({"description": "A <!-- hidden --> mug"}, "contains HTML"),
        ({"description": "A &lt;script&gt;x&lt;/script&gt; mug"}, "contains HTML"),
        ({"description": "A &amp;lt;b&amp;gt; mug"}, "contains HTML"),
        ({"description": "Click javascript:alert(1)"}, "contains HTML"),
        ({"description": "Click JAVA\u200bSCRIPT : now"}, "contains HTML"),
        ({"description": "See [the shop](https://x.example/a)"}, "contains HTML"),
        ({"description": "See ![pic](a.png)"}, "contains HTML"),
        ({"description": "Visit https://example.com now"}, "contains a URL"),
        ({"description": "An eco-friendly mug"}, "environmental claim"),
        ({"description": "Fits all models"}, "fitment claim"),
        ({"description": "Lorem ipsum dolor"}, "placeholder"),
        ({"description": "Order at shop.example.com today"}, "contains a URL"),
        ({"description": "Write to sales@example.org"}, "contains a URL"),
        ({"description": "An ECO\u200b-friendly mug"}, "environmental claim"),
        ({"description": "An \uff25\uff23\uff2f-friendly mug"}, "environmental claim"),
        ({"title": "Mug\nSecond line"}, "line break"),
        ({"description": "Price tbd"}, "placeholder"),
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


def test_a_word_containing_tbd_is_not_a_placeholder() -> None:
    text, _ = eng.validate_schema(good(description="Matchbd rack for the kitchen"), "en")
    assert text is not None
    assert eng.check_policy(text, POLICY) == []


def test_a_partial_limit_map_still_has_the_hard_ceiling() -> None:
    policy = TextPolicy(max_lengths={})
    text, errors = eng.validate_schema(good(seo_title="x" * 121), "en")
    assert text is None
    assert any("hard limit" in e for e in errors)
    ok, _ = eng.validate_schema(good(title="y" * 255, description="d" * 10_000), "en")
    assert ok is not None
    assert eng.check_policy(ok, policy) == []
    over, _ = eng.validate_schema(good(), "en")
    assert over is not None
    assert eng.check_policy(over, TextPolicy(max_lengths={"title": 3}))  # a tighter limit wins


def test_a_missing_key_is_a_schema_error_not_an_empty_text() -> None:
    raw = dict(good())
    del raw["short_description"]
    text, errors = eng.validate_schema(raw, "en")
    assert text is None
    assert "short_description: missing" in errors


@pytest.mark.parametrize(
    "sentence",
    [
        "Unsustainable growth is not our topic, this is a mug",
        "A todos-style note rack",
        "Matchbd rack for the kitchen",
        "The fits-the-hand grip, nothing to do with a bike",
    ],
)
def test_word_lists_match_whole_words_only(sentence: str) -> None:
    text, _ = eng.validate_schema(good(description=sentence), "en")
    assert text is not None
    assert eng.check_policy(text, POLICY) == []


def test_the_facts_and_the_limits_are_read_only() -> None:
    facts = ProductFacts(sku="S", title="T", attributes={"a": "b"})
    with pytest.raises(TypeError):
        facts.attributes["x"] = "y"  # type: ignore[index]


def test_default_limits_cannot_be_changed_in_place() -> None:
    with pytest.raises(TypeError):
        POLICY.max_lengths["title"] = 1000  # type: ignore[index]


# --- The one gate (lane-a review of #1150)


def test_the_gate_has_three_outcomes() -> None:
    accepted = eng.gate(good(), "en", POLICY)
    assert (accepted.outcome, accepted.violations) == ("accepted", [])
    assert accepted.text is not None

    broken = eng.gate({**good(), "extra": "x"}, "en", POLICY)
    assert (broken.text, broken.outcome) == (None, "schema_invalid")
    assert broken.violations == ["unknown field: extra"]

    outside = eng.gate(good(description="An eco-friendly mug"), "en", POLICY)
    assert outside.outcome == "out_of_policy"
    assert outside.text is not None  # the schema held; the text must still not be used
    assert any("environmental" in v for v in outside.violations)


def test_the_gate_applies_the_policy_to_every_language_it_is_given() -> None:
    assert eng.gate(good(), "it", TextPolicy(languages=("en",))).outcome == "out_of_policy"


def test_the_gate_is_the_schema_then_the_policy_and_nothing_else() -> None:
    raw = good(title="Visit www.example.com")
    text, errors = eng.validate_schema(raw, "en")
    assert errors == []
    assert text is not None  # the schema alone lets it through
    assert eng.gate(raw, "en", POLICY).outcome == "out_of_policy"  # the gate does not


# --- Immutable value objects


def test_the_provider_answer_cannot_be_changed_after_the_call() -> None:
    output = FakeEnricher().describe(FACTS, "en", POLICY)
    with pytest.raises(TypeError):
        output.raw["title"] = "changed"  # type: ignore[index]


def test_the_policy_coerces_its_sequences_to_tuples() -> None:
    policy = TextPolicy(languages=["en"], required=["title"], banned_terms=["x"])  # type: ignore[arg-type]
    assert (policy.languages, policy.required, policy.banned_terms) == (("en",), ("title",), ("x",))
    with pytest.raises(TypeError):
        TextPolicy(languages="en")  # type: ignore[arg-type]  # not ("e", "n")


def test_an_empty_language_entry_counts_as_not_written() -> None:
    assert eng.can_write_language("en", POLICY, {"en": {}})
    assert not eng.can_write_language("en", POLICY, {"en": {"title": "x"}})
