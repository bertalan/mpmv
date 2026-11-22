"""CMP.25 / I18N.19: the system check on the vendor self-certification texts.

A configured language without a text, or with a text no translator approved, is an error on
LIVE and a warning elsewhere (the pattern of core.E915 / core.W915)."""

import pytest
from django.core.checks import Error, Warning
from django.core.checks.registry import registry
from django.test import override_settings

from apps.vendors import engine
from apps.vendors.checks import check_self_certification_texts


def _ids(codes: list[str], *, live: bool) -> list[str]:
    with override_settings(LANGUAGES=[(code, code) for code in codes], LIVE_DATA=live):
        return [str(message.id) for message in check_self_certification_texts(None)]


def test_the_check_is_registered() -> None:
    registered = {getattr(check, "__name__", "") for check in registry.get_checks()}
    assert "check_self_certification_texts" in registered


def test_the_ungated_languages_pass_on_live() -> None:
    assert _ids(["en", "it"], live=True) == []
    assert _ids(["it", "en"], live=False) == []


def test_a_language_without_a_text_is_an_error_on_live() -> None:
    with override_settings(LANGUAGES=[("en", "en"), ("xx", "xx")], LIVE_DATA=True):
        messages = check_self_certification_texts(None)

    assert [type(message) for message in messages] == [Error]
    assert messages[0].id == "vendors.E001"
    assert "'xx'" in messages[0].msg


def test_a_language_without_a_text_is_a_warning_elsewhere() -> None:
    with override_settings(LANGUAGES=[("en", "en"), ("xx", "xx")], LIVE_DATA=False):
        messages = check_self_certification_texts(None)

    assert [type(message) for message in messages] == [Warning]
    assert messages[0].id == "vendors.W001"


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_text_counts_as_missing(monkeypatch: pytest.MonkeyPatch, empty: str) -> None:
    monkeypatch.setattr(
        engine, "SELF_CERTIFICATION_TEXTS", {**engine.SELF_CERTIFICATION_TEXTS, "it": empty}
    )

    assert _ids(["en", "it"], live=True) == ["vendors.E001"]


def test_an_unreviewed_text_is_an_error_on_live_and_a_warning_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "SELF_CERTIFICATION_REVIEWED_LANGUAGES", frozenset({"en"}))

    assert _ids(["en", "it"], live=True) == ["vendors.E002"]
    assert _ids(["en", "it"], live=False) == ["vendors.W002"]


def test_a_reviewed_language_passes_on_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine, "SELF_CERTIFICATION_REVIEWED_LANGUAGES", frozenset({"en", "it", "de"})
    )

    assert _ids(["en", "it", "de"], live=True) == []


def test_the_review_mark_does_not_stand_in_for_a_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine, "SELF_CERTIFICATION_REVIEWED_LANGUAGES", frozenset({"en", "it", "xx"})
    )

    assert _ids(["en", "xx"], live=True) == ["vendors.E001"]


def test_a_language_is_reported_once_and_missing_comes_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "SELF_CERTIFICATION_REVIEWED_LANGUAGES", frozenset({"en"}))

    assert _ids(["en", "it", "xx", "it"], live=True) == ["vendors.E001", "vendors.E002"]


def test_the_translated_go_live_languages_are_still_gated() -> None:
    # de, fr and es are AI drafts until the owner's translator approves them. When that
    # happens, move the language to the constant and out of this test in the same commit.
    assert engine.self_certification_languages_unreviewed(("en", "it", "de", "fr", "es")) == (
        "de",
        "fr",
        "es",
    )
    assert _ids(["en", "it", "de", "fr", "es"], live=True) == ["vendors.E002"] * 3
