"""ORD.17: the pure rules of the checkout legal step (box 2 matrix, D-40 counsel variant A)."""

import itertools

import pytest

from apps.orders import legal_engine as engine

CLAUSES = (("7", "Limitation of liability"),)


def platform(operator_country: str) -> engine.Party:
    return engine.Party("platform", "Operator", operator_country, "", CLAUSES)


def seller(country: str, law: str) -> engine.Party:
    return engine.Party("seller", "Seller", country, law, CLAUSES)


def shown(parties: list[engine.Party], *, business: bool, operator: str) -> list[str]:
    return [
        p.kind for p in engine.box2_parties(parties, business=business, operator_country=operator)
    ]


@pytest.mark.parametrize(
    ("operator", "seller_country", "seller_law", "expected"),
    [
        ("IT", "IT", "IT", ["platform", "seller"]),
        ("IT", "DE", "DE", ["platform"]),  # Italian operator, foreign seller: platform list only
        ("IT", "DE", "IT", ["platform", "seller"]),  # a foreign seller choosing Italian law
        ("DE", "IT", "DE", ["seller"]),  # a foreign operator, a seller established in Italy
        ("DE", "DE", "IT", ["seller"]),  # a foreign operator, a foreign seller, Italian law
        ("DE", "DE", "DE", []),  # nothing Italian: no box 2
        ("FR", "ES", "ES", []),
    ],
)
def test_a_business_sees_box_2_only_with_an_italian_party(
    operator: str, seller_country: str, seller_law: str, expected: list[str]
) -> None:
    parties = [platform(operator), seller(seller_country, seller_law)]
    assert shown(parties, business=True, operator=operator) == expected


@pytest.mark.parametrize(
    ("operator", "seller_country", "seller_law"),
    list(itertools.product(("IT", "DE"), ("IT", "DE"), ("IT", "DE"))),
)
def test_a_consumer_never_sees_box_2(operator: str, seller_country: str, seller_law: str) -> None:
    parties = [platform(operator), seller(seller_country, seller_law)]
    assert shown(parties, business=False, operator=operator) == []
    assert not engine.box2_required(parties, business=False, operator_country=operator)


def test_only_the_italian_sellers_of_the_cart_are_listed() -> None:
    parties = [platform("DE"), seller("IT", "IT"), seller("FR", "FR"), seller("DE", "IT")]
    listed = engine.box2_parties(parties, business=True, operator_country="DE")
    assert [p.established_country for p in listed] == ["IT", "DE"]


def test_a_business_is_one_who_chose_it_and_gave_a_vat_number() -> None:
    assert engine.buyer_is_business(chose_business=True, vat_id="DE123456789")
    assert not engine.buyer_is_business(chose_business=True, vat_id="  ")
    assert not engine.buyer_is_business(chose_business=False, vat_id="DE123456789")


def test_a_box_that_is_not_shown_is_never_required() -> None:
    assert engine.acceptance_errors(box1=True, box2=False, box2_shown=False) == []
    assert engine.acceptance_errors(box1=True, box2=False, box2_shown=True) == ["box2"]
    assert engine.acceptance_errors(box1=False, box2=True, box2_shown=True) == ["box1"]


def test_box_2_leaves_out_an_italian_party_with_no_onerous_clauses() -> None:
    """Box 2 is a specific approval: an Italian party with nothing listed is not shown (review)."""
    empty = engine.Party("platform", "Operator", "IT", "", ())
    assert engine.box2_parties([empty], business=True, operator_country="IT") == []
    assert not engine.box2_required([empty], business=True, operator_country="IT")
