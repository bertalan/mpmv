"""SEO.7: the schema check refuses incomplete Article, ProfilePage and Event nodes."""

from typing import Any

import pytest

from apps.seo import schema_check


def errors(node: dict[str, Any]) -> list[str]:
    return schema_check.rich_result_errors({"@graph": [node]})


def test_a_complete_article_profile_and_event_pass() -> None:
    assert errors({"@type": "Article", "headline": "H", "datePublished": "2026-01-01"}) == []
    assert errors({"@type": "ProfilePage", "mainEntity": {"@id": "x"}}) == []
    event = {"@type": "Event", "name": "E", "startDate": "2026-01-01", "location": {"name": "L"}}
    assert errors(event) == []


@pytest.mark.parametrize("kind", ["Article", "BlogPosting"])
@pytest.mark.parametrize("missing", ["headline", "datePublished"])
def test_an_article_without_a_required_property_is_refused(kind: str, missing: str) -> None:
    node = {"@type": kind, "headline": "H", "datePublished": "2026-01-01"}
    del node[missing]
    assert errors(node) == [f"{kind}: {missing} is missing"]


def test_a_profile_page_without_a_main_entity_is_refused() -> None:
    assert errors({"@type": "ProfilePage"}) == ["ProfilePage: mainEntity is missing"]


@pytest.mark.parametrize("missing", ["name", "startDate", "location"])
def test_an_event_without_a_required_property_is_refused(missing: str) -> None:
    node = {"@type": "Event", "name": "E", "startDate": "2026-01-01", "location": {"name": "L"}}
    del node[missing]
    assert errors(node) == [f"Event: {missing} is missing"]
