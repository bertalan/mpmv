"""CMP.3: warnings and safety information per language, the product page block and the
publication gate on the catalog's save (spec 11 CMP.3, GPSR art. 19).

The product page itself is CAT.7: it renders `{% product_safety product %}`. Until CAT.10 adds
the publishing services, `catalog.services.save_product` is the only way to publish, so it runs
the registered publication checks when a product becomes published.
"""

import datetime as dt
import logging

import pytest
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.utils import translation

from apps.catalog.models import Product
from apps.catalog.services import save_product
from apps.catalog.tests.factories import ProductFactory
from apps.compliance import services, tasks
from apps.compliance.models import GpsrInfo, GpsrSafetyText
from apps.compliance.tests.factories import (
    GpsrInfoFactory,
    GpsrSafetyTextFactory,
    VendorGpsrDefaultsFactory,
)
from apps.core.audit import AuditContext
from apps.core.mail import html_violations
from apps.core.models import AuditLog, OutboxEmail
from apps.core.services import render_email
from apps.core.tasks import schedules
from apps.core.tests.factories import PlatformOperatorFactory

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


def _published_complete() -> Product:
    defaults = VendorGpsrDefaultsFactory.create()
    product = ProductFactory.create(
        vendor=defaults.vendor, gtin="8001234567890", status=Product.Status.PUBLISHED
    )
    assert services.gpsr_publication_blockers(product) == ()
    return product


def test_an_unrelated_edit_of_a_published_product_is_saved() -> None:
    product = _published_complete()
    product.weight_kg = product.weight_kg
    save_product(product, ctx=CTX)


def test_an_edit_that_drops_the_gpsr_identifier_of_a_published_product_is_refused() -> None:
    product = _published_complete()
    product.gtin = ""
    with pytest.raises(ValidationError) as exc:
        save_product(product, ctx=CTX, dry_run=True)
    assert "gpsr" in exc.value.message_dict
    with pytest.raises(ValidationError):
        save_product(product, ctx=CTX)
    assert Product.objects.get(pk=product.pk).gtin == "8001234567890"


def test_unpublishing_an_incomplete_product_is_allowed() -> None:
    product = ProductFactory.create(gtin="", status=Product.Status.PUBLISHED)
    product.status = Product.Status.DRAFT
    save_product(product, ctx=CTX)
    assert Product.objects.get(pk=product.pk).status == Product.Status.DRAFT


def test_a_published_product_already_incomplete_can_still_be_edited() -> None:
    # Only a change that makes the data incomplete is refused; products that already fail the
    # check (imported before CMP.3) are listed by the nightly consistency check instead.
    product = ProductFactory.create(gtin="", status=Product.Status.PUBLISHED)
    product.weight_kg = product.weight_kg
    save_product(product, ctx=CTX)


def test_emptying_the_product_gpsr_of_a_published_product_is_refused() -> None:
    defaults = VendorGpsrDefaultsFactory.create()
    product = ProductFactory.create(vendor=defaults.vendor, gtin="")
    services.set_product_gpsr(product, {"model_number": "M-1"}, ctx=CTX)
    product.status = Product.Status.PUBLISHED
    save_product(product, ctx=CTX)
    with pytest.raises(ValidationError) as exc:
        services.set_product_gpsr(product, {"model_number": ""}, ctx=CTX, dry_run=True)
    assert "gpsr" in exc.value.message_dict
    with pytest.raises(ValidationError):
        services.set_product_gpsr(product, {"model_number": ""}, ctx=CTX)
    assert GpsrInfo.objects.get(product=product).model_number == "M-1"
    # The same edit on a draft is fine.
    draft = ProductFactory.create(vendor=product.vendor, gtin="")
    services.set_product_gpsr(draft, {"model_number": "M-2"}, ctx=CTX)
    services.set_product_gpsr(draft, {"model_number": ""}, ctx=CTX)


def test_emptying_vendor_defaults_used_by_a_published_product_is_refused() -> None:
    product = _published_complete()
    with pytest.raises(ValidationError) as exc:
        services.set_vendor_gpsr_defaults(
            product.vendor, {"manufacturer_name": ""}, ctx=CTX, dry_run=True
        )
    assert product.sku in str(exc.value)
    # A product with its own manufacturer block does not depend on the defaults.
    services.set_product_gpsr(
        product,
        {
            "manufacturer_name": "Own Maker S.r.l.",
            "manufacturer_address": "Via Esempio 2, 00100 Roma",
            "manufacturer_contact": "own@maker.example",
            "manufacturer_country": "IT",
        },
        ctx=CTX,
    )
    services.set_vendor_gpsr_defaults(product.vendor, {"manufacturer_name": ""}, ctx=CTX)


# --- Nightly consistency check: published products that fail the GPSR check


def test_the_nightly_check_lists_published_products_failing_gpsr(
    caplog: pytest.LogCaptureFixture,
) -> None:
    PlatformOperatorFactory.create(
        open_ended=True,
        valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        compliance_email="compliance@platform.example",
    )
    failing = ProductFactory.create(gtin="", status=Product.Status.PUBLISHED)
    _published_complete()
    ProductFactory.create(gtin="")  # a draft: not checked
    preview = services.flag_published_products_failing_gpsr(dry_run=True)
    assert [product.pk for product, _codes in preview] == [failing.pk]
    assert not OutboxEmail.objects.exists()

    with caplog.at_level(logging.ERROR, logger="apps.compliance"):
        tasks.check_published_gpsr.func(timestamp=0)
    assert failing.sku in caplog.text
    email = OutboxEmail.objects.get()
    assert email.recipient == "compliance@platform.example"
    assert email.template_key == services.PUBLISHED_INCOMPLETE_EMAIL_TEMPLATE
    tasks.check_published_gpsr.func(timestamp=0)
    assert OutboxEmail.objects.count() == 1  # once a day
    assert "compliance.check_published_gpsr" in {s.task_name for s in schedules()}


def test_the_nightly_email_renders() -> None:
    product = ProductFactory.create(gtin="", status=Product.Status.PUBLISHED)
    rows = services.published_products_failing_gpsr()
    rendered = render_email(
        services.PUBLISHED_INCOMPLETE_EMAIL_TEMPLATE,
        "en",
        services.published_incomplete_email_context(rows),
    )
    assert product.sku in rendered.text_body
    assert html_violations(rendered.html_body) == []


# --- The warnings are always shown when they exist (GPSR art. 19(c))


def test_display_falls_back_to_a_language_of_the_destination() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="de", warnings="Achtung.")
    GpsrSafetyTextFactory.create(product=product, language="fr", warnings="Attention.")
    shown = services.gpsr_display(product, "it", country="AT")
    assert (shown.warnings, shown.text_language) == ("Achtung.", "de")


def test_display_falls_back_to_any_available_language() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="de", warnings="Achtung.")
    shown = services.gpsr_display(product, "it")
    assert (shown.warnings, shown.text_language) == ("Achtung.", "de")
    assert shown.text_language_name


def test_the_block_labels_warnings_in_another_language() -> None:
    product = ProductFactory.create()
    GpsrSafetyTextFactory.create(product=product, language="de", warnings="Achtung.")
    template = Template("{% load compliance_tags %}{% product_safety product %}")
    with translation.override("it"):
        html = template.render(Context({"product": product}))
    assert "Achtung." in html
    assert 'lang="de"' in html
    assert "product-safety-language" in html
