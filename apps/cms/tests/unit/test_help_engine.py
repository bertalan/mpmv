"""ARC.32: the pure part of the help lookup (route name, then page type, then the general entry)."""

import pytest

from apps.cms import engine
from apps.cms.engine import HelpRow


def row(kind: str, key: str, language: str = "en", title: str = "") -> HelpRow:
    return HelpRow(kind=kind, key=key, language=language, title=title or f"{kind}:{key}:{language}")


@pytest.mark.parametrize(
    ("view_name", "expected"),
    [
        ("catalog:brand", "catalog:brand"),
        ("home", "home"),
        ("wagtail_serve", ""),  # the CMS catch-all names no screen: the page type does
        ("wagtail_serve_something", ""),
        ("", ""),
        (None, ""),
        ("has space", ""),
        ("x" * 200, ""),
    ],
)
def test_the_route_name_of_a_request(view_name: str | None, expected: str) -> None:
    assert engine.help_route_name(view_name) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [("cms.StandardPage", "cms.StandardPage"), ("", ""), (None, ""), ("a b", ""), ("<x>", "")],
)
def test_the_page_type_key(label: str | None, expected: str) -> None:
    assert engine.help_page_type(label) == expected


def test_candidates_run_from_the_most_specific_to_the_general_entry() -> None:
    assert engine.help_candidates("catalog:brand", "cms.StandardPage") == [
        ("route", "catalog:brand"),
        ("page_type", "cms.StandardPage"),
        ("general", ""),
    ]


def test_candidates_skip_what_the_request_does_not_have() -> None:
    assert engine.help_candidates("", "") == [("general", "")]
    assert engine.help_candidates("home", "") == [("route", "home"), ("general", "")]


def test_the_route_entry_beats_the_page_type_and_the_general_entry() -> None:
    rows = [row("general", ""), row("page_type", "cms.StandardPage"), row("route", "home")]
    found = engine.pick_help_row(
        rows, engine.help_candidates("home", "cms.StandardPage"), "en", "en"
    )
    assert found is not None
    assert (found.kind, found.key) == ("route", "home")


def test_the_page_type_entry_is_used_when_no_route_entry_exists() -> None:
    rows = [row("general", ""), row("page_type", "cms.StandardPage")]
    found = engine.pick_help_row(
        rows, engine.help_candidates("home", "cms.StandardPage"), "en", "en"
    )
    assert found is not None
    assert found.kind == "page_type"


def test_the_general_entry_answers_when_nothing_matches() -> None:
    rows = [row("general", ""), row("route", "other")]
    found = engine.pick_help_row(
        rows, engine.help_candidates("home", "cms.StandardPage"), "en", "en"
    )
    assert found is not None
    assert found.kind == "general"


def test_nothing_is_found_without_a_general_entry() -> None:
    assert engine.pick_help_row([], engine.help_candidates("home", ""), "en", "en") is None


def test_a_level_prefers_the_language_then_the_primary_language() -> None:
    rows = [row("route", "home", "en"), row("route", "home", "it"), row("general", "", "it")]
    found = engine.pick_help_row(rows, engine.help_candidates("home", ""), "it", "en")
    assert found is not None
    assert found.language == "it"
    found = engine.pick_help_row(rows, engine.help_candidates("home", ""), "de", "en")
    assert found is not None
    assert (found.kind, found.language) == ("route", "en")


def test_another_languages_text_is_never_shown_for_a_missing_primary_one() -> None:
    rows = [row("route", "home", "it")]
    assert engine.pick_help_row(rows, [("route", "home")], "de", "en") is None
