"""CMS.17: the pure rules of the footer columns and social profiles."""

import pytest

from apps.cms import engine


def test_the_network_list_is_closed_and_has_a_name_for_each() -> None:
    assert engine.SOCIAL_NETWORKS
    assert all(label for label in engine.SOCIAL_NETWORKS.values())


@pytest.mark.parametrize(
    "url", ["https://example.org/page", "https://www.example.org/@me", "https://example.org:8443/x"]
)
def test_an_absolute_https_profile_url_is_accepted(url: str) -> None:
    assert engine.social_url_error(url) == ""


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.org/page",
        "//example.org/page",
        "/relative",
        "https://user:secret@example.org/",
        "https://",
        "data:text/html,x",
        "",
    ],
)
def test_anything_but_an_absolute_https_url_is_refused(url: str) -> None:
    assert engine.social_url_error(url)


def test_a_language_shows_at_most_four_columns_in_order() -> None:
    rows = [("it", f"c{n}") for n in range(6)] + [("en", "other")]
    assert engine.footer_columns(rows, "it") == [f"c{n}" for n in range(4)]
    assert engine.footer_columns(rows, "de") == []


def test_profiles_apply_to_their_language_or_to_all_when_blank() -> None:
    rows = [("", "all"), ("it", "it-only"), ("en", "en-only")]
    assert engine.footer_profiles(rows, "it") == ["all", "it-only"]


def test_more_than_four_columns_in_one_language_is_an_error() -> None:
    assert engine.too_many_columns(["it"] * 5 + ["en"] * 4) == ["it"]
    assert engine.too_many_columns(["it"] * 4) == []
