"""I18N.10 (slice 1): free text is stored in Unicode NFC at the lane-b service boundaries.

A decomposed input ("e" + combining acute) through each wired form, service and API is stored
composed. The SKU stays as entered; its match key stays NFKC (spec 03).
"""

from decimal import Decimal
from typing import Any

import pytest
from django import forms
from django.test import Client
from wagtail.admin.panels import get_edit_handler

from apps.api.tests.factories import ApiClientFactory
from apps.catalog import engine, publishing, services
from apps.catalog.models import Product, ProductTranslation
from apps.cms.models import BlogAuthor, FormPage
from apps.cms.tests.factories import FormFieldFactory, FormPageFactory
from apps.core.audit import AuditContext
from apps.core.i18n import primary_language
from apps.core.nfc_forms import NFCFormMixin
from apps.core.text import nfc, nfc_deep
from apps.events.models import EventPage
from apps.events.services import save_event_draft
from apps.events.tests.factories import primary_index
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import save_storefront_profile
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

DECOMPOSED = "Café Città"
COMPOSED = "Café Città"
CTX = AuditContext(actor_label="test", source="test")


def test_the_helpers_compose_and_are_idempotent() -> None:
    assert nfc(DECOMPOSED) == COMPOSED
    assert nfc(COMPOSED) == COMPOSED
    assert nfc("") == ""
    assert nfc(None) is None
    nested = {"a": [DECOMPOSED, {"b": (DECOMPOSED, 3, None)}], "ḱ": "x"}
    assert nfc_deep(nested) == {"a": [COMPOSED, {"b": (COMPOSED, 3, None)}], "ḱ": "x"}
    assert nfc_deep(nfc_deep(nested)) == nfc_deep(nested)


def test_a_form_with_the_mixin_cleans_text_to_nfc() -> None:
    class Plain(NFCFormMixin, forms.Form):
        text = forms.CharField(required=False)
        number = forms.IntegerField(required=False)

    form = Plain({"text": DECOMPOSED, "number": "3"})
    assert form.is_valid()
    assert form.cleaned_data == {"text": COMPOSED, "number": 3}
    empty = Plain({"text": ""})
    assert empty.is_valid()
    assert empty.cleaned_data["text"] == ""


def test_the_sku_match_key_stays_nfkc() -> None:
    assert engine.sku_match_key("\ufb01t-1") == "fit-1"
    assert engine.sku_match_key("\uff22\uff21\uff27-1") == "bag-1"


def _item(sku: str = "A-1") -> engine.ProductIn:
    return engine.ProductIn(
        sku=sku,
        price_net=Decimal("10.00"),
        weight_kg=Decimal("0.500"),
        translations=(
            engine.TranslationIn(
                primary_language(),
                DECOMPOSED,
                short_description=DECOMPOSED,
                description=f"<p>{DECOMPOSED}</p>",
                material=DECOMPOSED,
                colour=DECOMPOSED,
            ),
        ),
    )


def test_catalog_create_and_update_store_nfc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publishing, "_CHECKS", {})
    vendor = VendorFactory.create()
    sku = "Café-1"
    services.upsert_products(vendor, [_item(sku)], ctx=CTX)
    text = ProductTranslation.objects.get(product__vendor=vendor)
    assert (text.title, text.short_description, text.material, text.colour) == (COMPOSED,) * 4
    assert text.description == f"<p>{COMPOSED}</p>"
    assert Product.objects.get(vendor=vendor).sku == sku  # the SKU is kept as entered

    changed = engine.ProductIn(
        sku=sku,
        price_net=Decimal("10.00"),
        weight_kg=Decimal("0.500"),
        translations=(engine.TranslationIn(primary_language(), "Nouvellé"),),
    )
    services.upsert_products(vendor, [changed], ctx=CTX)
    assert ProductTranslation.objects.get(product__vendor=vendor).title == "Nouvell\u00e9"
    # Identical NFC text after the first save: nothing to change on a second run.
    again = services.upsert_products(vendor, [_item("B-1")], ctx=CTX)
    assert again.created == ["B-1"]


def test_catalog_create_product_directly_stores_nfc() -> None:
    vendor = VendorFactory.create()
    services.create_product(vendor, _item(), ctx=CTX)
    assert ProductTranslation.objects.get(product__vendor=vendor).title == COMPOSED


def test_the_storefront_profile_stores_nfc() -> None:
    vendor = VendorFactory.create()
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    save_storefront_profile(
        vendor,
        primary_language(),
        {"tagline": DECOMPOSED, "refund_policy": f"<p>{DECOMPOSED}</p>"},
        actor=owner,
        ctx=CTX,
    )
    row = VendorTranslation.objects.get(vendor=vendor, language=primary_language())
    assert row.tagline == COMPOSED
    assert row.refund_policy == f"<p>{COMPOSED}</p>"


def test_vendor_save_stores_nfc() -> None:
    from apps.vendors.services import save_vendor

    vendor = VendorFactory.create()
    vendor.display_name = DECOMPOSED
    save_vendor(vendor, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.display_name == COMPOSED


def test_an_event_draft_and_its_rich_text_are_stored_in_nfc() -> None:
    import datetime as dt

    primary_index()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    starts = dt.datetime(2027, 3, 10, 8, tzinfo=dt.UTC)
    data: dict[str, Any] = {
        "title": DECOMPOSED,
        "summary": DECOMPOSED,
        "event_type": EventPage.EventType.FAIR,
        "starts_at": starts,
        "ends_at": starts + dt.timedelta(hours=4),
        "timezone": "Europe/Rome",
        "attendance": EventPage.Attendance.IN_PERSON,
        "venue_name": DECOMPOSED,
        "city": DECOMPOSED,
        "country": "IT",
        "body": [{"type": "paragraph", "value": f"<p>{DECOMPOSED}</p>"}],
    }
    page = save_event_draft(owner, data, vendor=vendor).page
    assert page is not None
    page = EventPage.objects.get(pk=page.pk)
    assert (page.title, page.summary, page.venue_name, page.city) == (COMPOSED,) * 4
    assert COMPOSED in str(page.get_latest_revision_as_object().body)


def test_the_api_stores_nfc(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publishing, "_CHECKS", {})
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    _, key = ApiClientFactory.create_with_key(
        vendor=vendor, scopes=["catalog:read", "catalog:write"]
    )
    payload = {
        "sku": "Café-9",
        "price_net": "10.00",
        "weight_kg": "0.500",
        "translations": [{"language": primary_language(), "title": DECOMPOSED}],
    }
    response = client.post(
        "/api/v1/catalog/products",
        payload,
        content_type="application/json",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 201, response.content
    assert ProductTranslation.objects.get(product__vendor=vendor).title == COMPOSED
    assert Product.objects.get(vendor=vendor).sku == "Café-9"


def test_a_cms_form_page_submission_is_cleaned_to_nfc() -> None:
    page = FormPageFactory.create(title="Ask us")
    FormFieldFactory.create(page=page, label="Your question", field_type="singleline")
    form_class = FormPage.objects.get(pk=page.pk).get_form_class()
    form = form_class({"your_question": DECOMPOSED})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["your_question"] == COMPOSED


def test_cms_snippet_forms_are_nfc() -> None:
    form_class = get_edit_handler(BlogAuthor).get_form_class()
    assert issubclass(form_class, NFCFormMixin)
