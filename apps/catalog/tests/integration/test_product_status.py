"""CAT.11: bulk status change of products through the audited service."""

import pytest
from django.core.exceptions import ValidationError

from apps.catalog import publishing, services
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor=None)


@pytest.fixture(autouse=True)
def _no_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publishing, "_CHECKS", {})


def test_a_dry_run_changes_and_audits_nothing() -> None:
    product = ProductFactory.create()
    changes = services.set_products_status([product], "published", ctx=CTX, dry_run=True)
    assert changes.changes[0].after["status"] == "published"
    product.refresh_from_db()
    assert product.status == "draft"
    assert not AuditLog.objects.filter(action=services.PRODUCT_STATUS_CHANGED).exists()


def test_apply_changes_status_and_writes_one_audit_row_each() -> None:
    products = ProductFactory.create_batch(2)
    services.set_products_status(products, "published", ctx=CTX)
    assert set(Product.objects.values_list("status", flat=True)) == {"published"}
    rows = AuditLog.objects.filter(action=services.PRODUCT_STATUS_CHANGED)
    assert rows.count() == 2
    assert rows.first().before["status"] == "draft"  # type: ignore[union-attr]


def test_a_product_already_in_the_status_is_not_a_change() -> None:
    product = ProductFactory.create()
    assert services.set_products_status([product], "draft", ctx=CTX).is_empty


def test_a_failing_publication_check_refuses_the_whole_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publishing.register_publication_check("t", lambda p: ["blocked"])
    products = ProductFactory.create_batch(2)
    with pytest.raises(ValidationError):
        services.set_products_status(products, "published", ctx=CTX)
    assert set(Product.objects.values_list("status", flat=True)) == {"draft"}


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(ValueError, match="status"):
        services.set_products_status([ProductFactory.create()], "bogus", ctx=CTX)
