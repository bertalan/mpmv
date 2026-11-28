"""SEO.6 2b: a product or variant change purges the public pages (a shown price is never stale)."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from apps.catalog.models import Product
from apps.catalog.services import set_product_price
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core import public_cache
from apps.core.audit import AuditContext

pytestmark = pytest.mark.django_db


@pytest.fixture
def purges(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(public_cache, "_listeners", [seen.append])
    return seen


CTX = AuditContext(actor_label="user:staff@example.com", source="admin")
Commit = Callable[..., Any]


def test_creating_and_changing_a_product_purge(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        product = ProductFactory.create()
    assert "product" in purges
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        set_product_price(product, price_net=Decimal("12.00"), ctx=CTX)
    assert purges == ["product"]


def test_a_price_dry_run_purges_nothing(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    product = ProductFactory.create()
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        set_product_price(product, price_net=Decimal("99.00"), ctx=CTX, dry_run=True)
    assert purges == []


def test_deleting_a_product_purges(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    product = ProductFactory.create()
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        Product.objects.filter(pk=product.pk).first().delete()  # type: ignore[union-attr]
    assert purges == ["product"]


def test_a_variant_change_purges(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        variant = VariantFactory.create()
    assert "variant" in purges
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        variant.save()
    assert purges == ["variant"]
