"""Test data builders for core models (synthetic values only)."""

import datetime as dt
from typing import Any

from apps.core.models import PlatformOperator


def utc(year: int, month: int = 1, day: int = 1, hour: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, tzinfo=dt.UTC)


def operator(**overrides: Any) -> PlatformOperator:
    """An unsaved PlatformOperator with synthetic, valid configuration."""
    values: dict[str, Any] = {
        "legal_name": "Example Marketplace Ltd",
        "country": "AT",
        "vat_number": "ATU00000000",
        "tax_regime": PlatformOperator.TaxRegime.SMALL_BUSINESS,
        "commission_vat_mode": PlatformOperator.CommissionVatMode.NONE,
        "einvoice_channel": PlatformOperator.EInvoiceChannel.NONE,
        "dac7_authority_country": "AT",
        "valid_from": utc(2027),
    }
    values.update(overrides)
    return PlatformOperator(**values)
