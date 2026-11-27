"""ING.7: the pure plan that turns an ingest draft into a catalog payload (EN only, D-18 bis)."""

from decimal import Decimal

from apps.ingest import engine

LANG = "en"


def plan(extracted: dict, enriched: dict | None = None, sku: str = "SUP-1"):  # type: ignore[no-untyped-def]
    return engine.plan_draft_product(sku, extracted, enriched or {}, language=LANG)


def test_the_payload_takes_the_enriched_text_of_the_publish_language() -> None:
    result = plan(
        {"title": "Raw", "price": "12.50", "weight_kg": "0.4"},
        {LANG: {"title": "Nice", "short_description": "Short", "description": "Long"}},
    )
    assert not isinstance(result, engine.DraftProblem)
    assert result.sku == "SUP-1"
    assert result.price_net == Decimal("12.50")
    assert result.weight_kg == Decimal("0.4")
    assert [(t.language, t.title, t.short_description) for t in result.translations] == [
        (LANG, "Nice", "Short")
    ]


def test_other_languages_in_the_draft_are_never_published() -> None:
    result = plan(
        {"title": "Raw", "price": "1"},
        {LANG: {"title": "Nice"}, "it": {"title": "Bello"}, "de": {"title": "Schoen"}},
    )
    assert not isinstance(result, engine.DraftProblem)
    assert {t.language for t in result.translations} == {LANG}


def test_without_enriched_text_the_extracted_title_and_description_are_used() -> None:
    result = plan({"title": "Raw", "description": "Plain", "price": "3"})
    assert not isinstance(result, engine.DraftProblem)
    assert (result.translations[0].title, result.translations[0].description) == ("Raw", "Plain")


def test_the_cat22_fields_are_carried() -> None:
    result = plan(
        {
            "title": "Raw",
            "price": "3",
            "length_mm": 10,
            "width_mm": 20,
            "height_mm": 30,
            "country_of_origin": "DE",
            "material": "steel",
            "colour": "red",
        }
    )
    assert not isinstance(result, engine.DraftProblem)
    assert (result.length_mm, result.width_mm, result.height_mm) == (10, 20, 30)
    assert result.country_of_origin == "DE"
    assert (result.translations[0].material, result.translations[0].colour) == ("steel", "red")


def test_a_draft_without_a_title_or_a_valid_price_is_a_problem() -> None:
    assert isinstance(plan({"price": "3"}), engine.DraftProblem)
    assert isinstance(plan({"title": "T"}), engine.DraftProblem)
    assert isinstance(plan({"title": "T", "price": "abc"}), engine.DraftProblem)
    assert isinstance(plan({"title": "T", "price": "-1"}), engine.DraftProblem)
    assert isinstance(plan({"title": "T", "price": "3", "weight_kg": "x"}), engine.DraftProblem)


def test_only_ready_drafts_are_published() -> None:
    assert engine.is_publishable_status("ready")
    for status in ("new", "needs_review", "published", "rejected"):
        assert not engine.is_publishable_status(status)
