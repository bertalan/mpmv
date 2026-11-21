"""CMP.41 follow-up (owner decision 2026-11-19): a seller who sells to consumers cannot publish
a product without a phone number. The check belongs to vendors and is registered through
`catalog.publishing`. Fictional numbers only: the UK drama range +44 20 7946 0xxx (Ofcom)."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import translation

from apps.catalog import publishing
from apps.catalog import services as catalog_services
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.vendors.models import Vendor

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="test", source="test")
CHECK = "vendor_phone"


@pytest.fixture(autouse=True)
def _only_the_phone_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Other apps' checks (GPSR, fitment) are not under test here."""
    monkeypatch.setattr(publishing, "_CHECKS", {CHECK: publishing._CHECKS[CHECK]})


def product(phone: str, **fields: object) -> Product:
    return ProductFactory.create(
        vendor__phone=phone,
        vendor__status=Vendor.Status.ACTIVE,
        languages=[primary_language()],
        **fields,
    )


def publish(item: Product) -> None:
    report = catalog_services.publish([item], ctx=CTX)
    if report.refused:
        reasons = next(iter(report.refused.values()))
        raise ValidationError({name: messages for name, messages in reasons.items()})


def test_a_seller_to_consumers_without_a_phone_cannot_publish() -> None:
    item = product("")
    with translation.override("en"), pytest.raises(ValidationError) as caught:
        publish(item)
    assert CHECK in caught.value.message_dict
    assert "phone number" in " ".join(caught.value.message_dict[CHECK])
    assert Product.objects.get(pk=item.pk).status == "draft"


def test_the_message_is_translated_formally() -> None:
    with translation.override("de"):
        messages = publishing.publication_blockers(product(""))[CHECK]
    assert "Sie" in messages[0]


def test_a_seller_with_a_phone_publishes() -> None:
    item = product("+44 20 7946 0123")
    publish(item)
    assert Product.objects.get(pk=item.pk).status == "published"


def test_a_published_product_of_a_seller_without_a_phone_can_still_be_edited() -> None:
    item = product("", status="published")
    item.weight_kg = Decimal("0.750")
    catalog_services.save_product(item, ctx=CTX)
    assert Product.objects.get(pk=item.pk).status == "published"
