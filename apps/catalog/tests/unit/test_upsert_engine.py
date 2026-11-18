"""CAT.10: the pure planning rules of upserts and publishing."""

from decimal import Decimal

from apps.catalog import engine


def product(**kw: object) -> engine.ProductIn:
    return engine.ProductIn(sku="A", price_net=Decimal("1"), **kw)  # type: ignore[arg-type]


def test_field_changes_only_lists_differences() -> None:
    stored = {
        "sales_mode": "buy",
        "fiscal_category": "standard",
        "weight_kg": Decimal("0.500"),
        "length_mm": None,
        "width_mm": None,
        "height_mm": None,
        "country_of_origin": "",
    }
    assert engine.product_field_changes(stored, product(weight_kg=Decimal("0.5"))) == {}
    assert engine.product_field_changes(stored, product(weight_kg=Decimal("1"))) == {
        "weight_kg": Decimal("1")
    }


def test_translation_plan_splits_new_and_changed() -> None:
    stored = {"en": dict.fromkeys(engine.UPSERT_TEXT_FIELDS, "x")}
    same = engine.TranslationIn("en", "x", "x", "x", "x", "x")
    new, changed = engine.translation_plan(stored, [same, engine.TranslationIn("it", "y")])
    assert [t.language for t in new] == ["it"]
    assert changed == {}
    _, changed = engine.translation_plan(
        stored, [engine.TranslationIn("en", "z", "x", "x", "x", "x")]
    )
    assert changed == {"en": {"title": "z"}}


def test_variant_plan_finds_changes_and_unknown_skus() -> None:
    stored = {
        "a-1": dict.fromkeys(engine.UPSERT_VARIANT_FIELDS, None)
        | {"stock_qty": 1, "is_default": False, "gtin": "", "mpn": ""}
    }
    given = [engine.VariantIn("A-1", {}, stock_qty=2), engine.VariantIn("B", {})]
    changes, unknown = engine.variant_plan(stored, given)
    assert changes == {"a-1": {"stock_qty": 2}}
    assert unknown == ["B"]


def test_publication_errors() -> None:
    assert engine.publication_errors("buy", Decimal("1"), Decimal("1"), ["en"], "en") == []
    assert len(engine.publication_errors("buy", None, None, [], "en")) == 3
    assert engine.publication_errors("catalog_only", None, None, ["en"], "en") == []
