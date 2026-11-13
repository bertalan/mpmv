"""VEN.6 / D-19: the sales mode buyers get, from the product, the vendor and its payout account."""

import pytest

from apps.vendors import engine


@pytest.mark.parametrize(
    ("product", "vendor", "ready", "expected"),
    [
        ("buy", "buy", True, "buy"),
        ("buy", "buy", False, "on_request"),  # no payout account ready: on request (D-19)
        ("buy", "on_request", True, "on_request"),  # the vendor's mode overrides the product
        ("on_request", "buy", True, "on_request"),
        ("catalog_only", "buy", True, "catalog_only"),
        ("catalog_only", "on_request", False, "catalog_only"),  # never turned into an enquiry
    ],
)
def test_the_effective_sales_mode(product: str, vendor: str, ready: bool, expected: str) -> None:
    assert engine.effective_sales_mode(product, vendor, payout_ready=ready) == expected


def test_the_vendor_sells_online_only_with_a_ready_payout_account() -> None:
    assert engine.vendor_sales_mode("buy", payout_ready=True) == "buy"
    assert engine.vendor_sales_mode("buy", payout_ready=False) == "on_request"
    assert engine.vendor_sales_mode("on_request", payout_ready=True) == "on_request"
