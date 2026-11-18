"""VEN.40: the pure rules of a vendor's return policy (no database)."""

from typing import Any

import pytest

from apps.vendors import return_engine as engine


def values(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "return_window_days": 30,
        "return_shipping_paid_by": "vendor",
        "return_methods": ["post", "drop_off"],
        "returns_page_url": "https://shop.example/returns",
        "return_country": "IT",
    }
    return base | over


def test_a_valid_policy_has_no_errors() -> None:
    assert engine.policy_errors(values()) == {}


def test_13_days_is_refused_and_14_is_accepted() -> None:
    assert "return_window_days" in engine.policy_errors(values(return_window_days=13))
    assert engine.policy_errors(values(return_window_days=14)) == {}


@pytest.mark.parametrize("days", [0, -5, None, "abc", 3651, True])
def test_a_window_that_is_not_a_sane_integer_is_refused(days: Any) -> None:
    assert "return_window_days" in engine.policy_errors(values(return_window_days=days))


def test_the_shipping_payer_is_customer_or_vendor() -> None:
    assert "return_shipping_paid_by" in engine.policy_errors(values(return_shipping_paid_by="x"))
    assert engine.policy_errors(values(return_shipping_paid_by="customer")) == {}


def test_methods_are_a_non_empty_subset_of_the_known_ones() -> None:
    assert "return_methods" in engine.policy_errors(values(return_methods=[]))
    assert "return_methods" in engine.policy_errors(values(return_methods=["carrier_pigeon"]))
    assert engine.policy_errors(values(return_methods=["post", "in_store"])) == {}


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example/returns",
        "ftp://shop.example/returns",
        "javascript:alert(1)",
        "https://",
        "https://user:pw@shop.example/returns",
        "https://shop.example/a b",
        "//shop.example/returns",
        "https://" + "a" * 600 + ".example",
    ],
)
def test_the_returns_page_must_be_a_plain_https_url(url: str) -> None:
    assert "returns_page_url" in engine.policy_errors(values(returns_page_url=url))


def test_the_returns_page_is_optional() -> None:
    assert engine.policy_errors(values(returns_page_url="")) == {}


def test_the_country_must_be_an_iso_code() -> None:
    assert "return_country" in engine.policy_errors(values(return_country="ZZZ"))
    assert "return_country" in engine.policy_errors(values(return_country=""))


def test_the_default_is_the_14_day_floor_with_the_customer_paying() -> None:
    default = engine.default_policy("DE")
    assert default.return_window_days == 14
    assert default.return_shipping_paid_by == "customer"
    assert default.return_country == "DE"
    assert default.is_default is True
