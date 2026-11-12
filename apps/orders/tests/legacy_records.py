"""Legacy order records as the MIG.1 extractor yields them (plain dicts), for the MIG.4 tests.

Synthetic values only. `record()` is a closed, fully taxed order: one vendor part with a 2 x 10.00
line at 22% (tax 4.40) and 5.00 shipping at 22% (tax 1.10). Net 25.00, tax 5.50, total 30.50.
"""

from typing import Any

# One value per personal or technical field the archive must never keep.
CANARIES = {
    "shipping_address_1": "CANARY-SHIP-STREET",
    "shipping_phone": "CANARY-PHONE",
    "customer_ip": "CANARY-IP",
    "customer_user_agent": "CANARY-UA",
    "customer_note": "CANARY-NOTE",
    "order_key": "CANARY-TOKEN",
    "email": "canary-mail@example.invalid",
    "geolocation": "CANARY-GEO",
    "attribution": "CANARY-UTM",
}

COMMISSION = {
    "rule_id": "legacy-plan-a",
    "version": 1,
    "percent": "12.00",
    "base": "goods",
    "fee_percent": "0.00",
    "fee_fixed": "0.00",
    "fee_base": "gross",
}


def record(legacy_id: int = 4711, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "legacy_order_id": legacy_id,
        "status": "completed",
        "created_at": "2024-03-10T09:30:00+00:00",
        "paid_at": "2024-03-10T09:35:00+00:00",
        "billing": {
            "name": "Mario Rossi",
            "company": "",
            "address_1": "1 Via Roma",
            "address_2": "",
            "city": "Roma",
            "postcode": "00100",
            "state": "RM",
            "country": "IT",
            "vat_id": "IT12345678901",
        },
        "shipping_country": "IT",
        "tax_total": "5.50",
        "vendors": [
            {
                "vendor_key": "A",
                "lines": [
                    {
                        "sku": "CUP-1",
                        "title": "Ceramic cup",
                        "variant_label": "Blue",
                        "quantity": 2,
                        "unit_price_net": "10.00",
                        "discount_net": "0.00",
                        "tax_rate": "22",
                        "tax_reason": "domestic",
                    }
                ],
                "shipping_net": "5.00",
                "shipping_tax_rate": "22",
                "shipping_tax_reason": "domestic",
                "commission": dict(COMMISSION),
            }
        ],
        "refunds": [],
        **CANARIES,
    }
    base["billing"] = {**base["billing"], **overrides.pop("billing", {})}
    base.update(overrides)
    return base
