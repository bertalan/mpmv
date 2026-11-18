"""FIT.18: the publish gate for undeclared products (spec 17 §17.2.4, D-38)."""

import pytest
from django.core.exceptions import ValidationError
from django.utils import translation

from apps.catalog import publishing
from apps.catalog import services as catalog_services
from apps.catalog.models import Product
from apps.catalog.tests.factories import CategoryFactory, ProductFactory
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.models import AuditLog
from apps.fitment import services
from apps.fitment.models import FitmentCategory
from apps.fitment.tests.factories import FitmentCategoryFactory, ProductFitmentStateFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="test", source="test")


def product_in(category: object, **fields: object) -> Product:
    return ProductFactory.create(category=category, languages=[primary_language()], **fields)


@pytest.fixture
def gated() -> object:
    """A category whose setting is on."""
    return FitmentCategoryFactory.create(require_declaration=True).category


def edited_to_published(product: Product) -> None:
    product.status = "published"
    catalog_services.save_product(product, ctx=CTX)


def bulk(product: Product) -> None:
    catalog_services.set_products_status([product], "published", ctx=CTX)


def publish_service(product: Product) -> None:
    report = catalog_services.publish([product], ctx=CTX)
    if report.refused:
        reasons = next(iter(report.refused.values()))
        raise ValidationError({name: messages for name, messages in reasons.items()})


PATHS = [edited_to_published, bulk, publish_service]


@pytest.mark.parametrize("publish", PATHS)
def test_an_undeclared_buy_product_is_refused_with_what_to_declare(
    publish: object, gated: object
) -> None:
    product = product_in(gated)
    with pytest.raises(ValidationError) as caught:
        publish(product)  # type: ignore[operator]
    text = " ".join(str(m) for messages in caught.value.message_dict.values() for m in messages)
    assert "fits" in text
    assert Product.objects.get(pk=product.pk).status == "draft"


@pytest.mark.parametrize("publish", PATHS)
@pytest.mark.parametrize("mode", ["specific", "universal"])
def test_a_declared_product_publishes(publish: object, gated: object, mode: str) -> None:
    product = product_in(gated)
    ProductFitmentStateFactory.create(product=product, mode=mode)
    publish(product)  # type: ignore[operator]
    assert Product.objects.get(pk=product.pk).status == "published"


@pytest.mark.parametrize("publish", PATHS)
def test_an_explicitly_undeclared_state_row_is_refused_too(publish: object, gated: object) -> None:
    product = product_in(gated)
    ProductFitmentStateFactory.create(product=product, mode="undeclared")
    with pytest.raises(ValidationError):
        publish(product)  # type: ignore[operator]


@pytest.mark.parametrize("publish", PATHS)
def test_with_the_setting_off_nothing_changes(publish: object) -> None:
    relevant_only = FitmentCategoryFactory.create(require_declaration=False).category
    for category in (relevant_only, CategoryFactory.create()):
        product = product_in(category)
        publish(product)  # type: ignore[operator]
        assert Product.objects.get(pk=product.pk).status == "published"


@pytest.mark.parametrize("publish", PATHS)
def test_a_product_without_a_category_is_not_gated(publish: object, gated: object) -> None:
    product = product_in(None)
    publish(product)  # type: ignore[operator]
    assert Product.objects.get(pk=product.pk).status == "published"


def test_a_catalog_only_product_is_not_gated(gated: object) -> None:
    product = product_in(gated, sales_mode="catalog_only", weight_kg=None)
    bulk(product)
    assert Product.objects.get(pk=product.pk).status == "published"


def test_a_published_product_stays_editable_when_it_was_already_undeclared(
    gated: object,
) -> None:
    product = product_in(gated, status="published")
    product.sku = "SKU-EDITED"
    catalog_services.save_product(product, ctx=CTX)
    assert Product.objects.get(pk=product.pk).sku == "SKU-EDITED"


def test_the_gate_is_a_registered_check_not_a_catalog_import() -> None:
    assert "fitment" in publishing._CHECKS


@pytest.mark.parametrize(
    ("language", "needle"),
    [("it", "Indichi"), ("de", "Geben Sie")],
)
def test_the_message_is_translated_and_formal(language: str, needle: str, gated: object) -> None:
    product = product_in(gated)
    with translation.override(language):
        blockers = publishing.publication_blockers(product)
    assert needle in " ".join(blockers["fitment"])


# --- The setting (service)


def test_the_setting_is_off_by_default_and_set_in_bulk_with_audit() -> None:
    first, second = CategoryFactory.create(), CategoryFactory.create()
    services.set_fitment_relevant([first], True, ctx=CTX)
    assert FitmentCategory.objects.get(category=first).require_declaration is False
    dry = services.set_fitment_publish_gate([first, second], True, ctx=CTX, dry_run=True)
    assert not dry.is_empty
    assert not FitmentCategory.objects.filter(require_declaration=True).exists()
    services.set_fitment_publish_gate([first, second], True, ctx=CTX)
    assert set(
        FitmentCategory.objects.filter(require_declaration=True).values_list(
            "category_id", flat=True
        )
    ) == {first.pk, second.pk}
    assert AuditLog.objects.filter(action=services.CATEGORY_PUBLISH_GATE_CHANGED).count() == 2
    assert services.set_fitment_publish_gate([first], True, ctx=CTX).is_empty


def test_switching_the_setting_off_keeps_the_category_relevant() -> None:
    category = CategoryFactory.create()
    services.set_fitment_publish_gate([category], True, ctx=CTX)
    services.set_fitment_publish_gate([category], False, ctx=CTX)
    row = FitmentCategory.objects.get(category=category)
    assert row.require_declaration is False
