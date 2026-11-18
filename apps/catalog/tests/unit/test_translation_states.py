"""CAT.11: the pure per-language translation state of a product (no database)."""

from apps.catalog import engine


def test_every_language_is_missing_without_rows() -> None:
    states = engine.translation_states([], ["en", "it"], "en")
    assert states == {"en": "missing", "it": "missing"}


def test_status_is_reported_per_language() -> None:
    rows = [("en", "published", "h1"), ("it", "reviewed", "h1")]
    assert engine.translation_states(rows, ["en", "it", "de"], "en") == {
        "en": "published",
        "it": "reviewed",
        "de": "missing",
    }


def test_a_row_made_from_another_source_text_is_stale() -> None:
    rows = [("en", "published", "h2"), ("it", "published", "h1"), ("de", "machine", "h1")]
    states = engine.translation_states(rows, ["en", "it", "de"], "en")
    assert states == {"en": "published", "it": "stale", "de": "stale"}


def test_a_complete_product_has_every_language_published_and_fresh() -> None:
    rows = [("en", "published", "h"), ("it", "published", "h")]
    assert engine.translations_complete(engine.translation_states(rows, ["en", "it"], "en"))
    rows[1] = ("it", "reviewed", "h")
    assert not engine.translations_complete(engine.translation_states(rows, ["en", "it"], "en"))
