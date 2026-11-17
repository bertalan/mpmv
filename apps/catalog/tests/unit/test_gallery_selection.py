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
