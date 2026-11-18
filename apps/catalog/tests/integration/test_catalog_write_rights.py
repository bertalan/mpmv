"""CAT.30 (MQ-52): Support reads the platform catalog; every write service refuses it.

Per role and per write service: Support, Finance and Configuration are refused with
PermissionDenied before anything is written or audited; Catalog and moderation and the platform
owner role pass the service check (the Manager still lets the owner only read), and so does a
pipeline without a user.
"""

import contextlib
from collections.abc import Callable
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.tests.factories import UserFactory
from apps.catalog import media, publishing, services
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductImageFactory, RedirectFactory, VariantFactory
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


# --- every other write service (CAT.30): the guard is the first statement, before validation.


class World:
    """Rows the guarded calls need, created before the refusal test counts anything."""

    def __init__(self) -> None:
        self.variant = VariantFactory.create(product__languages=["en"])
        self.product = self.variant.product
        self.translation = self.product.translations.first()
        self.image = ProductImageFactory.create(product=self.product)
        self.redirect = RedirectFactory.create()
        self.vendor = self.product.vendor


@pytest.fixture
def world() -> World:
    return World()


def _guarded(w: World) -> dict[str, Callable[[AuditContext], Any]]:
    return {
        "create_product": lambda c: services.create_product(w.vendor, None, ctx=c),  # type: ignore[arg-type]
        "set_product_price": lambda c: services.set_product_price(
            w.product, price_net="9.00", ctx=c
        ),
        "save_variant": lambda c: services.save_variant(w.variant, ctx=c),
        "set_variant_price": lambda c: services.set_variant_price(
            w.variant, price_net_override=None, ctx=c
        ),
        "rename_product_sku": lambda c: services.rename_product_sku(w.product, "NEW-1", ctx=c),
        "rename_variant_sku": lambda c: services.rename_variant_sku(w.variant, "NEW-V1", ctx=c),
        "update_by_sku": lambda c: services.update_by_sku(w.vendor, [], ctx=c),
        "upsert_products": lambda c: services.upsert_products(w.vendor, [], ctx=c),
        "sync_product_slug": lambda c: services.sync_product_slug(w.translation, ctx=c),  # type: ignore[arg-type]
        "rebuild_product_slugs": lambda c: services.rebuild_product_slugs(ctx=c),
        "save_redirect": lambda c: services.save_redirect(w.redirect, ctx=c),
        "delete_redirect": lambda c: services.delete_redirect(w.redirect, ctx=c),
        "import_redirects": lambda c: services.import_redirects([], ctx=c),
        "set_cover": lambda c: media.set_cover(w.image, ctx=c),
        "lock_cover": lambda c: media.lock_cover(w.image, locked=True, ctx=c),
        "set_language": lambda c: media.set_language(w.image, None, ctx=c),
        "reorder": lambda c: media.reorder(w.product, [w.image], ctx=c),
        "delete_image": lambda c: media.delete_image(w.image, ctx=c),
        "set_text": lambda c: media.set_text(w.image, "en", alt_text="x", ctx=c),
        "upload_image": lambda c: media.upload_image(
            w.product, file=None, title="t", licence=None, author="a", ctx=c
        ),
    }


NAMES = list(_guarded(World.__new__(World)))


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("role", ["support", "finance", "config"])
def test_every_other_write_service_refuses_a_read_only_role(
    name: str, role: str, world: World
) -> None:
    ctx = ctx_of(role)
    audits, rows = AuditLog.objects.count(), Product.objects.count()
    with pytest.raises(PermissionDenied):
        _guarded(world)[name](ctx)
    assert (AuditLog.objects.count(), Product.objects.count()) == (audits, rows)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("role", ["catalog", "owner"])
def test_the_catalog_and_owner_roles_get_past_the_guard(name: str, role: str, world: World) -> None:
    """Other errors (empty payloads, bad arguments) are fine: only the role check is tested."""
    ctx = ctx_of(role)
    with contextlib.suppress(ValidationError, ValueError, TypeError, AttributeError):
        _guarded(world)[name](ctx)
