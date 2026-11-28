"""ING.12 done-when: the sample client works in dry-run (and applies only after a clean one)."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from django.test import Client

from apps.api.tests.factories import ApiClientFactory
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.i18n import primary_language
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

_PATH = Path(__file__).resolve().parents[4] / "tools" / "import_api_client.py"
_SPEC = importlib.util.spec_from_file_location("import_api_client", _PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(_PATH)
sample = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sample)


def _transport(client: Client, key: str) -> Any:
    def send(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = client.generic(
            method,
            path,
            json.dumps(body),
            content_type="application/json",
            headers={"Authorization": f"Bearer {key}"},
        )
        return response.status_code, response.json()

    return send


@pytest.fixture
def setup() -> tuple[Vendor, Any, dict[str, Any]]:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    existing = ProductFactory.create(vendor=vendor, price_net="5.00", stock_qty=0)
    _, key = ApiClientFactory.create_with_key(
        vendor=vendor, scopes=["catalog:write", "stock:write"]
    )
    feed = {
        "products": [
            {
                "sku": "NEW-1",
                "price_net": "9.90",
                "translations": [{"language": primary_language(), "title": "New"}],
            }
        ],
        "stock": [{"sku": existing.sku, "stock_qty": 4}],
        "prices": [{"sku": existing.sku, "price_net": "6.50"}],
    }
    return vendor, (existing, key), feed


def test_the_default_run_is_a_dry_run_and_writes_nothing(
    client: Client, setup: tuple[Vendor, Any, dict[str, Any]]
) -> None:
    _vendor, (existing, key), feed = setup
    lines = sample.run(feed, _transport(client, key))

    assert [line.split(":")[0] for line in lines] == [
        "dry-run products",
        "dry-run stock",
        "dry-run prices",
    ]
    assert not Product.objects.filter(sku="NEW-1").exists()
    existing.refresh_from_db()
    assert existing.stock_qty == 0
    assert str(existing.price_net) == "5.00"


def test_apply_writes_after_a_clean_dry_run(
    client: Client, setup: tuple[Vendor, Any, dict[str, Any]]
) -> None:
    vendor, (existing, key), feed = setup
    lines = sample.run(feed, _transport(client, key), apply=True)

    assert len(lines) == 6
    assert Product.objects.filter(vendor=vendor, sku="NEW-1").exists()
    existing.refresh_from_db()
    assert existing.stock_qty == 4
    assert str(existing.price_net) == "6.50"


def test_a_refused_row_stops_before_anything_is_applied(
    client: Client, setup: tuple[Vendor, Any, dict[str, Any]]
) -> None:
    _, (existing, key), feed = setup
    feed["products"].append({"sku": "BAD-1", "price_net": "-1.00", "translations": []})
    lines = sample.run(feed, _transport(client, key), apply=True)

    assert any("BAD-1" in line for line in lines)
    assert not Product.objects.filter(sku="NEW-1").exists()
    existing.refresh_from_db()
    assert existing.stock_qty == 0
