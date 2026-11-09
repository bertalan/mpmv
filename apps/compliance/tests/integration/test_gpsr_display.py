"""CMP.3: warnings and safety information per language, the product page block and the
publication gate on the catalog's save (spec 11 CMP.3, GPSR art. 19).

The product page itself is CAT.7: it renders `{% product_safety product %}`. Until CAT.10 adds
the publishing services, `catalog.services.save_product` is the only way to publish, so it runs
the registered publication checks when a product becomes published.
"""

import pytest
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.utils import translation

from apps.catalog.models import Product
from apps.catalog.services import save_product
from apps.catalog.tests.factories import ProductFactory
from apps.compliance import services
from apps.compliance.models import GpsrSafetyText
from apps.compliance.tests.factories import (
    GpsrInfoFactory,
    GpsrSafetyTextFactory,
    VendorGpsrDefaultsFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:compliance", source="admin", ip="127.0.0.1")


# --- Warnings and safety information, per language


def test_safety_text_is_saved_per_language_and_audited() -> None:
    product = ProductFactory.create()
    services.set_product_safety_text(
        product, "it", {"warnings": "Non adatto ai bambini sotto i 3 anni."}, ctx=CTX
    )
    services.set_product_safety_text(
        product, "en", {"warnings": "Not suitable for children under 3."}, ctx=CTX
    )
    assert GpsrSafetyText.objects.filter(product=product).count() == 2
    entry = AuditLog.objects.filter(action=services.SAFETY_TEXT_CREATED).first()
    assert entry is not None
    assert entry.vendor_key == str(product.vendor.public_id)

    services.set_product_safety_text(
        product, "en", {"safety_information": "Keep away from heat."}, ctx=CTX
    )
    row = GpsrSafetyText.objects.get(product=product, language="en")
    assert row.warnings == "Not suitable for children under 3."  # kept
    assert row.safety_information == "Keep away from heat."
    assert AuditLog.objects.filter(action=services.SAFETY_TEXT_UPDATED).count() == 1


def test_safety_text_dry_run_and_unknown_language() -> None:
    product = ProductFactory.create()
    services.set_product_safety_text(product, "de", {"warnings": "Achtung."}, ctx=CTX, dry_run=True)
    assert not GpsrSafetyText.objects.exists()
    with pytest.raises(ValidationError):
        services.set_product_safety_text(product, "xx", {"warnings": "?"}, ctx=CTX)


# --- What the product page shows


def test_display_merges_the_vendor_defaults_and_picks_the_language() -> None:
    defaults = VendorGpsrDefaultsFactory.create(manufacturer_name="Example Maker S.r.l.")
    product = ProductFactory.create(vendor=defaults.vendor, gtin="8001234567890")
    GpsrInfoFactory.create(product=product, model_number="M-1", batch="")
    services.set_product_safety_text(product, "it", {"warnings": "Attenzione."}, ctx=CTX)
    services.set_product_safety_text(product, "en", {"warnings": "Caution."}, ctx=CTX)

    shown = services.gpsr_display(product, "it")
    assert shown.parties.manufacturer_name == "Example Maker S.r.l."
    assert ("GTIN", "8001234567890") in shown.identifiers
    assert ("Model", "M-1") in shown.identifiers
    assert all(value for _label, value in shown.identifiers)  # empty ones are left out
    assert (shown.warnings, shown.text_language) == ("Attenzione.", "it")


def test_display_falls_back_to_the_primary_language() -> None:
    product = GpsrSafetyTextFactory.create(language="en", warnings="Caution.").product
    shown = services.gpsr_display(product, "de")
    assert (shown.warnings, shown.text_language) == ("Caution.", "en")


def test_the_product_page_block_renders_the_gpsr_data() -> None:
    defaults = VendorGpsrDefaultsFactory.create(
        manufacturer_name="Example Maker S.r.l.", manufacturer_contact="safety@maker.example"
    )
    product = ProductFactory.create(vendor=defaults.vendor, gtin="8001234567890")
    services.set_product_safety_text(product, "en", {"warnings": "Caution <hot>."}, ctx=CTX)
    template = Template("{% load compliance_tags %}{% product_safety product %}")
    with translation.override("de"):
        html = template.render(Context({"product": product}))
    assert "Example Maker S.r.l." in html
    assert "safety@maker.example" in html
    assert "8001234567890" in html
    assert "Caution &lt;hot&gt;." in html  # escaped
    assert 'lang="en"' in html  # the fallback text says its language


# --- The publication gate on the catalog's save


def test_save_product_refuses_to_publish_without_gpsr() -> None:
    product = ProductFactory.create(gtin="")
    product.status = Product.Status.PUBLISHED
    with pytest.raises(ValidationError) as exc:
        save_product(product, ctx=CTX, dry_run=True)
    assert "gpsr" in exc.value.message_dict
    with pytest.raises(ValidationError):
        save_product(product, ctx=CTX)
    assert Product.objects.get(pk=product.pk).status == Product.Status.DRAFT


def test_save_product_publishes_with_complete_gpsr() -> None:
    defaults = VendorGpsrDefaultsFactory.create()
    product = ProductFactory.create(vendor=defaults.vendor, gtin="8001234567890")
    product.status = Product.Status.PUBLISHED
    save_product(product, ctx=CTX)
    assert Product.objects.get(pk=product.pk).status == Product.Status.PUBLISHED


def test_other_edits_of_a_published_product_are_not_blocked() -> None:
    # Data that was complete when published and is incomplete now is reported by the
    # publication check, but an unrelated edit is never refused for it.
    product = ProductFactory.create(gtin="", status=Product.Status.PUBLISHED)
    product.weight_kg = product.weight_kg
    save_product(product, ctx=CTX)
