"""OP-28 (Omnibus): the reviews policy page explains how reviews are checked (pure, no database)."""

import pytest

from apps.cms import engine, seed


def test_the_reviews_page_is_a_go_live_legal_page() -> None:
    assert "reviews" in engine.GO_LIVE_LEGAL_KINDS


@pytest.mark.parametrize("language", ["en", "it", "de", "fr", "es"])
def test_every_language_has_a_title_and_slug_for_the_reviews_page(language: str) -> None:
    text = seed.legal_placeholder(language, "reviews")
    assert text.title
    assert text.slug


@pytest.mark.parametrize(
    ("language", "verified", "not_verified"),
    [("en", "Verified purchase", "Not verified"), ("it", "Acquisto verificato", "Non verificata")],
)
def test_the_draft_says_what_verified_means_and_that_migrated_reviews_are_not(
    language: str, verified: str, not_verified: str
) -> None:
    body = seed.legal_placeholder(language, "reviews").body
    assert verified in body
    assert not_verified in body
    assert "Placeholder" not in body
    assert "Segnaposto" not in body


def test_the_italian_draft_speaks_in_the_second_person_or_neutrally_never_formal_lei() -> None:
    body = seed.legal_placeholder("it", "reviews").body
    assert " Lei " not in body
    assert "La Sua" not in body
