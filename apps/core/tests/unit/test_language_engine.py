"""I18N.15 (3): the pure plan for removing a language or changing the primary one."""

import pytest

from apps.core.language_engine import (
    LanguageChangeError,
    plan_language_change,
    settings_conflict,
    url_prefix,
)


def test_prefix_is_empty_for_the_primary_language() -> None:
    assert url_prefix("en", "en") == "/"
    assert url_prefix("it", "en") == "/it/"


def test_removing_a_language_redirects_its_prefix_to_the_primary() -> None:
    plan = plan_language_change(["en", "it", "de"], ["en", "it"])
    assert plan.removed == ("de",)
    assert plan.added == ()
    assert plan.redirects == (("/de/", "/"),)
    assert not plan.primary_changed


def test_changing_the_primary_redirects_the_new_primary_prefix_to_root() -> None:
    plan = plan_language_change(["en", "it", "de"], ["it", "en", "de"])
    assert plan.primary_changed
    assert plan.removed == ()
    assert plan.redirects == (("/it/", "/"),)
    assert any("/en/" in note or "unprefixed" in note for note in plan.notes)


def test_removing_the_old_primary_while_changing_it() -> None:
    plan = plan_language_change(["en", "it", "de"], ["de", "it"])
    assert plan.removed == ("en",)
    assert plan.primary_changed
    assert plan.redirects == (("/de/", "/"),)


def test_adding_a_language_is_not_a_command_matter() -> None:
    plan = plan_language_change(["en", "it"], ["en", "it", "de"])
    assert plan.added == ("de",)
    assert plan.redirects == ()
    assert not plan.removed


def test_no_change_is_empty() -> None:
    assert plan_language_change(["en", "it"], ["en", "it"]).is_empty


@pytest.mark.parametrize("target", [[], ["en", "en"], ["en", "xx"]])
def test_bad_targets_are_refused(target: list[str]) -> None:
    with pytest.raises(LanguageChangeError):
        plan_language_change(["en", "it"], target)


def test_settings_equal_to_the_record_conflict_with_nothing() -> None:
    assert settings_conflict(["en", "it"], ["en", "it"]) is None


def test_settings_that_only_add_languages_are_fine() -> None:
    assert settings_conflict(["en", "it"], ["en", "it", "de"]) is None


def test_settings_that_drop_a_language_conflict() -> None:
    message = settings_conflict(["en", "it", "de"], ["en", "it"])
    assert message is not None
    assert "de" in message


def test_settings_that_change_the_primary_conflict() -> None:
    message = settings_conflict(["en", "it"], ["it", "en"])
    assert message is not None
    assert "primary" in message
