"""VEN.41: a business-only seller's package is refused to a buyer without a verified VAT number
(`engine.business_only_block`, pure)."""

from apps.orders import engine


def test_a_business_only_seller_refuses_a_buyer_without_a_verified_vat_number() -> None:
    block = engine.business_only_block(7, business_only=True, verified_business=False)
    assert block is not None
    assert block.code == engine.BUSINESS_ONLY
    assert block.vendor_id == 7
    assert "businesses only" in block.message


def test_a_verified_business_buys_from_a_business_only_seller() -> None:
    assert engine.business_only_block(7, business_only=True, verified_business=True) is None


def test_an_ordinary_seller_sells_to_everyone() -> None:
    assert engine.business_only_block(7, business_only=False, verified_business=False) is None
    assert engine.business_only_block(7, business_only=False, verified_business=True) is None
