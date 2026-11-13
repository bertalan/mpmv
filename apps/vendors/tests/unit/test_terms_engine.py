"""VEN.35: the pure rules of a seller's sales terms (D-40, counsel L-1 to L-6)."""

import pytest

from apps.vendors import terms_engine as te


def test_an_empty_return_cost_setting_means_the_seller_pays() -> None:
    assert te.consumer_bears_return_cost("", "") is False
    assert te.consumer_bears_return_cost("", "The buyer pays the carrier.") is False


def test_the_consumer_pays_only_when_the_terms_say_so_and_the_information_shows_it() -> None:
    assert te.consumer_bears_return_cost(te.CONSUMER, "The buyer pays the carrier.") is True
    assert te.consumer_bears_return_cost(te.CONSUMER, "") is False  # information missing
    assert te.consumer_bears_return_cost(te.CONSUMER, "   ") is False
    assert te.consumer_bears_return_cost(te.SELLER, "anything") is False


def test_the_b2b_court_is_never_given_for_a_consumer() -> None:
    assert te.competent_court("Milano", "Bergamo", buyer_is_business=False) is None
    assert te.competent_court("", "Bergamo", buyer_is_business=False) is None


def test_the_b2b_court_falls_back_to_the_registered_office() -> None:
    assert te.competent_court("Milano", "Bergamo", buyer_is_business=True) == "Milano"
    assert te.competent_court("", "Bergamo", buyer_is_business=True) == "Bergamo"
    assert te.competent_court("  ", "Bergamo", buyer_is_business=True) == "Bergamo"


def test_there_is_no_refund_handling_fee_in_the_rules() -> None:
    assert not [n for n in dir(te) if "fee" in n.lower() or "handling" in n.lower()]
    assert not [f for f in te.FIELDS if "fee" in f or "handling" in f or "refund" in f]


def _valid() -> dict[str, object]:
    return {
        "body": "Our terms.",
        "clauses": [{"article": "7", "title": "Limitation of liability"}],
        "governing_law": "IT",
        "b2b_court": "",
        "delivery_days": 5,
        "return_cost_bearer": "",
        "return_cost_info": "",
    }


def test_a_complete_version_has_no_errors() -> None:
    assert te.terms_errors(_valid()) == {}


@pytest.mark.parametrize("clauses", [[], None])
def test_a_version_without_the_clause_list_cannot_be_published(clauses: object) -> None:
    values = _valid() | {"clauses": clauses}
    assert "clauses" in te.terms_errors(values)


def test_a_clause_needs_an_article_number_and_a_title() -> None:
    values = _valid() | {"clauses": [{"article": "", "title": "x"}]}
    assert "clauses" in te.terms_errors(values)
    values = _valid() | {"clauses": [{"article": "3", "title": ""}]}
    assert "clauses" in te.terms_errors(values)


def test_the_body_the_law_and_the_delivery_time_are_required() -> None:
    errors = te.terms_errors(_valid() | {"body": " ", "governing_law": "", "delivery_days": None})
    assert {"body", "governing_law", "delivery_days"} <= set(errors)


def test_parse_clauses_reads_one_clause_per_line() -> None:
    text = "7 | Limitation of liability\n\n 12 - Court of the seller \n"
    assert te.parse_clauses(text) == [
        {"article": "7", "title": "Limitation of liability"},
        {"article": "12", "title": "Court of the seller"},
    ]


def test_the_content_hash_is_stable_and_changes_with_the_text() -> None:
    assert te.content_hash(_valid()) == te.content_hash(_valid())
    assert te.content_hash(_valid()) != te.content_hash(_valid() | {"body": "Other."})
