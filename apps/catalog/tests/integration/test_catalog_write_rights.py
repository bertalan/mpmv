"""CAT.30 (MQ-52): Support reads the platform catalog; every write service refuses it.

Per role and per write service: Support, Finance and Configuration are refused with
PermissionDenied before anything is written or audited; Catalog and moderation and the platform
owner role pass the service check (the Manager still lets the owner only read), and so does a
pipeline without a user.
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied

from apps.accounts.tests.factories import UserFactory
from apps.catalog import publishing, services
from apps.catalog.models import Product
from apps.catalog.tests.factories import VariantFactory
from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

Call = Callable[[AuditContext, Product], Any]


@pytest.fixture(autouse=True)
def _no_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publishing, "_CHECKS", {})


@pytest.fixture
def product() -> Product:
    return VariantFactory.create(product__languages=["en"]).product


def ctx_of(role: str) -> AuditContext:
    return user_context(UserFactory.create(platform_role=role), None)


def _save(ctx: AuditContext, product: Product) -> Any:
    product.sales_mode = "catalog_only"
    return services.save_product(product, ctx=ctx)


def _bulk(ctx: AuditContext, product: Product) -> Any:
    return services.set_products_status([product], "published", ctx=ctx)


def _publish(ctx: AuditContext, product: Product) -> Any:
    return services.publish([product], ctx=ctx)


def _retire(ctx: AuditContext, product: Product) -> Any:
    return services.retire([product], ctx=ctx)


def _delete(ctx: AuditContext, product: Product) -> Any:
    return services.delete_product(product, ctx=ctx)


def _delete_variant(ctx: AuditContext, product: Product) -> Any:
    return services.delete_variant(product.variants.get(), ctx=ctx)


WRITES = [_save, _bulk, _publish, _retire, _delete, _delete_variant]


@pytest.mark.parametrize("write", WRITES)
@pytest.mark.parametrize("role", ["support", "finance", "config"])
def test_a_read_only_staff_role_is_refused_and_nothing_is_written(
    write: Call, role: str, product: Product
) -> None:
    ctx = ctx_of(role)
    audits, products = AuditLog.objects.count(), list(Product.objects.values_list("pk", "status"))
    with pytest.raises(PermissionDenied):
        write(ctx, product)
    assert AuditLog.objects.count() == audits
    assert list(Product.objects.values_list("pk", "status")) == products


@pytest.mark.parametrize("write", WRITES)
@pytest.mark.parametrize("role", ["catalog", "owner"])
def test_the_catalog_and_owner_roles_may_write(write: Call, role: str, product: Product) -> None:
    write(ctx_of(role), product)


@pytest.mark.parametrize("write", WRITES)
def test_a_pipeline_without_a_user_may_write(write: Call, product: Product) -> None:
    write(AuditContext(actor_label="api:pipeline", source="api"), product)


def test_a_refused_dry_run_is_refused_too(product: Product) -> None:
    with pytest.raises(PermissionDenied):
        services.set_products_status([product], "published", ctx=ctx_of("support"), dry_run=True)
