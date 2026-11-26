"""SEO.16: the schema check refuses a property on a type that does not define it, or a value
outside its range (found by the schema.org validator on STAGE, 2026-11-25)."""

from typing import Any

import pytest

from apps.seo import schema_check

SITE = "https://shop.example"
ITEMS = {"@type": "ItemList", "@id": f"{SITE}/#list-1", "name": "Picks", "itemListElement": []}
FAQ = {"@type": "FAQPage", "@id": f"{SITE}/#faq", "inLanguage": "en", "mainEntity": []}


def errors(*nodes: dict[str, Any]) -> list[str]:
    return schema_check.rich_result_errors({"@graph": list(nodes)})


def page(**extra: Any) -> dict[str, Any]:
    return {"@type": "WebPage", "@id": f"{SITE}/#webpage", "inLanguage": "en", **extra}


def test_has_part_pointing_at_an_item_list_is_refused() -> None:
    found = errors(page(hasPart=[{"@id": ITEMS["@id"]}]), ITEMS)
    assert found == [f"WebPage {SITE}/#webpage: hasPart points at an ItemList, not a CreativeWork"]


def test_has_part_pointing_at_a_creative_work_passes() -> None:
    assert errors(page(hasPart=[{"@id": FAQ["@id"]}]), FAQ) == []


def test_an_inline_has_part_value_is_checked_too() -> None:
    found = errors(page(hasPart={"@type": "ItemList", "name": "Inline"}))
    assert found == [f"WebPage {SITE}/#webpage: hasPart points at an ItemList, not a CreativeWork"]


def test_a_reference_to_an_outside_page_is_not_judged() -> None:
    assert errors(page(hasPart=[{"@id": "https://elsewhere.example/#x"}])) == []


@pytest.mark.parametrize("kind", ["ItemList", "Organization", "Product", "ListItem"])
def test_in_language_on_a_type_without_it_is_refused(kind: str) -> None:
    node = {"@type": kind, "@id": f"{SITE}/#n", "name": "N", "inLanguage": "en"}
    found = [e for e in errors(node) if "inLanguage" in e]
    assert found == [f"{kind} {SITE}/#n: inLanguage is not a property of {kind}"]


def test_in_language_on_a_nested_node_is_refused() -> None:
    nested = page(about={"@type": "ItemList", "name": "Inner", "inLanguage": "en"})
    assert errors(nested) == ["ItemList (no @id): inLanguage is not a property of ItemList"]


@pytest.mark.parametrize(
    "kind", ["WebPage", "CollectionPage", "WebSite", "BlogPosting", "FAQPage", "Event"]
)
def test_in_language_on_a_type_that_has_it_passes(kind: str) -> None:
    assert [e for e in errors({"@type": kind, "inLanguage": "en"}) if "inLanguage" in e] == []
