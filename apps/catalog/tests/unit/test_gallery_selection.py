"""CAT.4: the pure image-selection rule (D-18, spec 03 §3.2, spec 09 §9.2)."""

from dataclasses import dataclass

from apps.catalog import engine


@dataclass(frozen=True)
class Row:
    name: str
    language: str | None = None
    is_cover: bool = False
    position: int = 0


def names(rows: list[Row]) -> list[str]:
    return [row.name for row in engine.gallery_selection(rows, "de")]


def test_a_language_with_dedicated_images_shows_only_those() -> None:
    rows = [Row("neutral-1"), Row("neutral-2", position=1), Row("de-1", "de")]
    assert names(rows) == ["de-1"]


def test_other_languages_show_the_neutral_images() -> None:
    rows = [Row("neutral-1"), Row("de-1", "de"), Row("neutral-2", position=1)]
    assert [r.name for r in engine.gallery_selection(rows, "fr")] == ["neutral-1", "neutral-2"]


def test_the_cover_comes_first_otherwise_the_first_by_position() -> None:
    rows = [Row("b", position=2), Row("a", position=1), Row("c", position=3, is_cover=True)]
    assert names_for(rows, "en") == ["c", "a", "b"]
    rows = [Row("b", position=2), Row("a", position=1)]
    assert names_for(rows, "en") == ["a", "b"]


def test_a_cover_of_another_language_does_not_count() -> None:
    rows = [Row("n1", position=1), Row("n2", position=2), Row("de", "de", is_cover=True)]
    assert names_for(rows, "en") == ["n1", "n2"]


def test_no_images_no_gallery() -> None:
    assert engine.gallery_selection([], "de") == []


def names_for(rows: list[Row], language: str) -> list[str]:
    return [row.name for row in engine.gallery_selection(rows, language)]


# --- CAT.26: the gallery of a selected variant


def variant_names(
    own: list[Row], shared: list[Row], language: str, cover: Row | None = None
) -> list[str]:
    return [row.name for row in engine.variant_gallery(own, shared, cover, language)]


def test_a_variant_gallery_shows_its_own_images_first_then_the_product_gallery() -> None:
    own = [Row("v2", position=2), Row("v1", position=1)]
    shared = [Row("p1"), Row("p2", position=1)]
    assert variant_names(own, shared, "en") == ["v1", "v2", "p1", "p2"]


def test_a_variant_without_images_has_no_gallery_of_its_own() -> None:
    assert variant_names([], [Row("p1")], "en") == []


def test_the_variant_cover_leads_the_variant_gallery() -> None:
    own = [Row("v1", position=1), Row("v2", position=2)]
    shared = [Row("p1")]
    cover = Row("v2", position=2)
    assert variant_names(own, shared, "en", cover) == ["v2", "v1", "p1"]


def test_a_cover_from_the_product_gallery_leads_and_is_not_repeated() -> None:
    shared = [Row("p1"), Row("p2", position=1)]
    cover = Row("p2", position=1)
    assert variant_names([], shared, "en", cover) == ["p2", "p1"]


def test_a_de_dedicated_variant_image_shows_in_de_only() -> None:
    own = [Row("v-neutral"), Row("v-de", "de")]
    shared = [Row("p1")]
    assert variant_names(own, shared, "de") == ["v-de", "p1"]
    assert variant_names(own, shared, "en") == ["v-neutral", "p1"]
    assert variant_names([Row("v-de", "de")], shared, "en") == []


def test_a_de_cover_is_ignored_in_another_language() -> None:
    cover = Row("v-de", "de", is_cover=True)
    assert variant_names([cover], [Row("p1")], "en", cover) == []


def test_the_product_gallery_part_follows_the_language_rule() -> None:
    own = [Row("v1")]
    shared = [Row("p-neutral"), Row("p-de", "de")]
    assert variant_names(own, shared, "de") == ["v1", "p-de"]
    assert variant_names(own, shared, "en") == ["v1", "p-neutral"]
