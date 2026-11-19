"""SEO.10 1/2: the effective role of a vendor for one product (pure rule)."""

from types import SimpleNamespace

import pytest

from apps.catalog.engine import effective_vendor_role


def _product(own: str, default: str) -> SimpleNamespace:
    return SimpleNamespace(vendor_role=own, vendor=SimpleNamespace(default_product_role=default))


@pytest.mark.parametrize(
    ("own", "default", "expected"),
    [
        ("reseller", "producer", "reseller"),  # the override wins
        ("producer", "reseller", "producer"),
        ("", "producer", "producer"),  # blank falls back to the vendor default
        ("", "reseller", "reseller"),
        ("", "", ""),  # unknown stays unknown
        ("bogus", "", ""),  # never promoted to a role
    ],
)
def test_effective_vendor_role(own: str, default: str, expected: str) -> None:
    assert effective_vendor_role(_product(own, default)) == expected
