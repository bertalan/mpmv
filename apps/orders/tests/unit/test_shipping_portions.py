"""ORD.29: the shipping VAT portions are the only source of truth for shipping VAT by rate.

`VendorOrder.shipping_tax_rate` is NULL for a package split across rates, so no reader may group
VAT by it (guard test); `check_shipping_portions` is the invariant the service and the v1 builder
hold.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from apps.orders import engine

D = Decimal
ROOT = Path(__file__).resolve().parents[4]
# Only these may name the vendor order's single shipping rate: the model, the service that
# writes it, and the v1 builder that reads it for a single-rate order (the portions otherwise).
WRITERS = {
    "apps/orders/models.py",
    "apps/orders/services.py",
    "apps/orders/versions.py",
}
RATE_FIELD = re.compile(r"shipping_tax_(rate|reason)\b")


def _sources() -> list[Path]:
    found = []
    for path in sorted((ROOT / "apps").rglob("*")):
        if path.suffix not in {".py", ".html", ".txt", ".xml", ".csv"}:
            continue
        parts = path.relative_to(ROOT).parts
        if "tests" in parts or "migrations" in parts:
            continue
        found.append(path)
    return found


def test_no_reader_groups_vat_by_the_vendor_order_rate() -> None:
    readers = sorted(
        str(path.relative_to(ROOT))
        for path in _sources()
        if RATE_FIELD.search(path.read_text(encoding="utf-8"))
        and str(path.relative_to(ROOT)) not in WRITERS
    )
    assert not readers, (
        "shipping VAT by rate must be read from the shipping portions (v1 snapshot or "
        f"shipping_choice['tax_portions']), never from VendorOrder.shipping_tax_rate: {readers}"
    )


def test_the_guard_scans_the_order_code() -> None:
    assert any(str(p.relative_to(ROOT)) == "apps/orders/documents.py" for p in _sources())


def test_portions_that_add_up_give_the_summed_vat() -> None:
    portions = [(D("5.00"), D("22"), D("1.10")), (D("5.00"), D("4"), D("0.20"))]
    assert engine.check_shipping_portions(D("10.00"), portions) == D("1.30")
    assert engine.check_shipping_portions(D("10.00"), portions, shipping_tax=D("1.30")) == D("1.30")


@pytest.mark.parametrize(
    ("net", "portions", "stored"),
    [
        (D("10.00"), [], None),
        (D("10.00"), [(D("5.00"), D("22"), D("1.10")), (D("4.00"), D("4"), D("0.16"))], None),
        (D("10.00"), [(D("5.00"), D("22"), D("1.10")), (D("5.00"), D("4"), D("0.50"))], None),
        (D("10.00"), [(D("5.00"), D("22"), D("1.10")), (D("5.00"), D("4"), D("0.20"))], D("1.40")),
    ],
)
def test_portions_that_do_not_add_up_are_refused(
    net: Decimal, portions: list[tuple[Decimal, Decimal, Decimal]], stored: Decimal | None
) -> None:
    with pytest.raises(engine.ShippingPortionsMismatch, match="shipping"):
        engine.check_shipping_portions(net, portions, shipping_tax=stored)


def test_a_portion_tax_is_rounded_half_up_per_portion() -> None:
    # 3.33 at 22 % = 0.7326 -> 0.73; 6.67 at 4 % = 0.2668 -> 0.27
    portions = [(D("3.33"), D("22"), D("0.73")), (D("6.67"), D("4"), D("0.27"))]
    assert engine.check_shipping_portions(D("10.00"), portions) == D("1.00")
