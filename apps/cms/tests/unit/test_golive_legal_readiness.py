"""I18N.9: the go-live readiness rule for customer legal and checkout pages (Q-42).

Pure rules (no database). At go-live, terms of sale, privacy, withdrawal, shipping and returns
are published in every go-live language, with no EN fallback; each language's published text
has its own native-speaker review and legal counsel check recorded (an AI draft is optional).
"""

from apps.cms.engine import (
    GO_LIVE_LEGAL_KINDS,
    REQUIRED_LEGAL_REVIEWS,
    PublishedLegalText,
    ReadinessProblem,
    legal_readiness_problems,
)

LANGUAGES = ("en", "it", "de", "fr", "es")
REVIEWED = frozenset({"native_review", "counsel_check"})


def _everything(reviews: frozenset[str] = REVIEWED) -> list[PublishedLegalText]:
    return [
        PublishedLegalText(kind=kind, language=language, version=1, reviews=reviews)
        for kind in GO_LIVE_LEGAL_KINDS
        for language in LANGUAGES
    ]


def test_the_go_live_pages_are_the_customer_legal_checkout_and_reviews_pages() -> None:
    # OP-28 adds the reviews page (Omnibus: how reviews are verified) to the original five.
    assert GO_LIVE_LEGAL_KINDS == (
        "terms",
        "privacy",
        "withdrawal",
        "shipping",
        "returns",
        "reviews",
    )


def test_native_review_and_counsel_check_are_required_and_the_ai_draft_is_not() -> None:
    assert set(REQUIRED_LEGAL_REVIEWS) == {"native_review", "counsel_check"}


def test_every_page_published_and_reviewed_in_every_language_is_ready() -> None:
    assert legal_readiness_problems(_everything(), GO_LIVE_LEGAL_KINDS, LANGUAGES) == []


def test_a_page_missing_in_one_language_is_a_problem_for_that_language_only() -> None:
    texts = [t for t in _everything() if (t.kind, t.language) != ("withdrawal", "de")]

    problems = legal_readiness_problems(texts, GO_LIVE_LEGAL_KINDS, LANGUAGES)

    assert problems == [ReadinessProblem(kind="withdrawal", language="de", missing="translation")]
    assert "withdrawal" in problems[0].message
    assert "de" in problems[0].message


def test_an_english_page_does_not_stand_in_for_a_missing_language() -> None:
    """No EN fallback for these pages (Q-42): EN alone covers EN only."""
    texts = [t for t in _everything() if t.language == "en"]

    problems = legal_readiness_problems(texts, GO_LIVE_LEGAL_KINDS, LANGUAGES)

    assert {(p.kind, p.language) for p in problems} == {
        (kind, language) for kind in GO_LIVE_LEGAL_KINDS for language in LANGUAGES[1:]
    }
    assert {p.missing for p in problems} == {"translation"}


def test_a_published_text_without_its_reviews_is_a_problem() -> None:
    texts = [
        PublishedLegalText(
            kind=t.kind,
            language=t.language,
            version=t.version,
            reviews=frozenset({"ai_draft", "native_review"})
            if (t.kind, t.language) == ("terms", "fr")
            else t.reviews,
        )
        for t in _everything()
    ]

    problems = legal_readiness_problems(texts, GO_LIVE_LEGAL_KINDS, LANGUAGES)

    assert problems == [ReadinessProblem(kind="terms", language="fr", missing="counsel_check")]


def test_a_reviewed_text_in_one_language_does_not_count_for_another() -> None:
    texts = _everything(reviews=frozenset())
    texts = [
        PublishedLegalText(t.kind, t.language, t.version, REVIEWED) if t.language == "en" else t
        for t in texts
    ]

    problems = legal_readiness_problems(texts, GO_LIVE_LEGAL_KINDS, LANGUAGES)

    assert {p.language for p in problems} == set(LANGUAGES[1:])
    assert len(problems) == len(GO_LIVE_LEGAL_KINDS) * 4 * len(REQUIRED_LEGAL_REVIEWS)


def test_pages_outside_the_go_live_set_are_ignored() -> None:
    cookies = PublishedLegalText(kind="cookies", language="en", version=1, reviews=frozenset())
    assert legal_readiness_problems([*_everything(), cookies], GO_LIVE_LEGAL_KINDS, LANGUAGES) == []


def test_problems_are_listed_by_page_then_language() -> None:
    problems = legal_readiness_problems([], ("terms", "privacy"), ("en", "it"))
    assert [(p.kind, p.language) for p in problems] == [
        ("terms", "en"),
        ("terms", "it"),
        ("privacy", "en"),
        ("privacy", "it"),
    ]
