"""ING.12 done-when: the sample client works in dry-run (and applies only after a clean one)."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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


@pytest.mark.parametrize(
    "url",
    ["http://shop.example.org", "ftp://localhost", "shop.example.org", "http://localhost.evil.org"],
)
def test_a_non_https_base_url_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="https"):
        sample.check_base_url(url)


@pytest.mark.parametrize(
    "url", ["https://shop.example.org", "http://localhost:8000", "http://127.0.0.1"]
)
def test_https_and_localhost_are_accepted(url: str) -> None:
    assert sample.check_base_url(url) == url


def test_a_non_json_error_body_does_not_crash_the_client() -> None:
    assert sample._json(b"<html>Bad gateway</html>") == {"detail": "<html>Bad gateway</html>"}
    assert sample._json(b"") == {}
    assert sample._json(b"[1]") == {"detail": "[1]"}


def test_a_200_answer_without_a_summary_does_not_crash_the_client() -> None:
    lines = sample.run({"products": [{"sku": "A"}]}, lambda *_: (200, {"rows": []}))
    assert len(lines) == 1
    assert "dry-run products" in lines[0]


def test_a_dry_run_404_names_the_skus_the_feed_creates() -> None:
    def send(method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if "products" in path:
            return 200, {"summary": "ok", "rows": []}
        return 404, {"detail": "The resource does not exist."}

    feed = {
        "products": [{"sku": "NEW-1"}],
        "stock": [{"sku": "new-1", "stock_qty": 1}, {"sku": "OLD-1", "stock_qty": 1}],
    }
    lines = sample.run(feed, send)
    assert lines[1].startswith("dry-run stock: refused 404")
    assert "created in this feed" in lines[2]
    assert "new-1" in lines[2]
    assert "OLD-1" not in lines[2]


def test_a_redirect_is_refused_and_the_key_is_not_sent_on() -> None:
    seen: list[str | None] = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    target = HTTPServer(("127.0.0.1", 0), Target)
    target_url = f"http://127.0.0.1:{target.server_port}/stolen"

    class Redirector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    origin = HTTPServer(("127.0.0.1", 0), Redirector)
    threads = [threading.Thread(target=s.handle_request, daemon=True) for s in (origin, target)]
    for thread in threads:
        thread.start()
    try:
        send = sample.http_transport(f"http://127.0.0.1:{origin.server_port}", "k-" + "x" * 8)
        status, answer = send("POST", "/api/v1/import/products", {"items": []})
    finally:
        origin.server_close()
        target.server_close()

    assert status == 302
    assert answer["detail"] == sample.REDIRECT_DETAIL
    assert seen == []
