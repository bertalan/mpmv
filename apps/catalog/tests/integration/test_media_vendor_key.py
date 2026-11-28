"""ING.12: `media.upload_image` for an API key checks the key against the product's vendor."""

import io
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog import media
from apps.catalog.models import ProductImage
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.tests.factories import ImageLicenceFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

API_CTX = AuditContext(actor_label="api:test", source="api")


def _png() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "teal").save(buffer, format="PNG")
    return SimpleUploadedFile("a.png", buffer.getvalue(), content_type="image/png")


def _upload(product: Any, key: media.VendorKey | None, ctx: AuditContext = API_CTX) -> None:
    licence = ImageLicenceFactory.create(code="own-work", name="Own", source_required=False)
    media.upload_image(
        product, file=_png(), title="t", licence=licence, author="a", ctx=ctx, key=key
    )


def _key(
    vendor: Vendor | None, *, active: bool = True, scopes: tuple[str, ...] = ("catalog:write",)
) -> media.VendorKey:
    return media.VendorKey(
        vendor_id=None if vendor is None else vendor.pk, scopes=frozenset(scopes), active=active
    )


def test_the_keys_own_vendor_may_upload() -> None:
    product = ProductFactory.create()
    _upload(product, _key(product.vendor))
    assert ProductImage.objects.filter(product=product).count() == 1


@pytest.mark.parametrize("case", ["other_vendor", "platform", "revoked", "no_scope"])
def test_any_other_key_is_refused_and_nothing_is_written(case: str) -> None:
    product = ProductFactory.create()
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    key = {
        "other_vendor": _key(other),
        "platform": _key(None),
        "revoked": _key(product.vendor, active=False),
        "no_scope": _key(product.vendor, scopes=("catalog:read",)),
    }[case]
    with pytest.raises(PermissionDenied):
        _upload(product, key)
    assert not ProductImage.objects.exists()


def test_a_key_with_a_non_api_source_is_refused() -> None:
    product = ProductFactory.create()
    ctx = AuditContext(actor_label="system:test", source="system")
    with pytest.raises(PermissionDenied):
        _upload(product, _key(product.vendor), ctx)
    assert not ProductImage.objects.exists()
