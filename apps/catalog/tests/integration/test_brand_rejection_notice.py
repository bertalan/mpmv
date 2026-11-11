"""CAT.20 (2/3), owner decision: rejecting a brand clears it from the products that used it, and
each affected vendor gets a notice naming the brand and its own products, through the outbox."""

import pytest

from apps.catalog import brands
from apps.catalog.models import Brand
from apps.catalog.tests.factories import BrandFactory, ProductFactory, ProductTranslationFactory
from apps.core.audit import AuditContext
from apps.core.models import OutboxEmail
from apps.core.services import render_email
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
TEMPLATE = "catalog.brand_rejected"


def _rejected_notices() -> list[OutboxEmail]:
    return list(OutboxEmail.objects.filter(template_key=TEMPLATE).order_by("id"))


def test_each_affected_vendor_gets_one_notice_with_its_own_products_only() -> None:
    mine, other = VendorFactory.create(), VendorFactory.create()
    brand = BrandFactory.create(name="Acmee", status=Brand.Status.PENDING, proposed_by=mine)
    own = ProductFactory.create_batch(2, vendor=mine, brand=brand)
    theirs = ProductFactory.create(vendor=other, brand=brand)
    unrelated = VendorFactory.create()
    brands.reject_brand(brand, ctx=CTX)
    notices = _rejected_notices()
    assert {n.recipient for n in notices} == {mine.contact_email, other.contact_email}
    assert unrelated.contact_email not in {n.recipient for n in notices}
    by_recipient = {n.recipient: n.context_data for n in notices}
    text = str(by_recipient[mine.contact_email])
    assert "Acmee" in text
    assert all(p.sku in text for p in own)
    assert theirs.sku not in text
    assert own[0].sku not in str(by_recipient[other.contact_email])
    assert theirs.sku in str(by_recipient[other.contact_email])


def test_the_notice_is_in_the_owners_language_with_english_fallback() -> None:
    vendor, other = VendorFactory.create(), VendorFactory.create()
    VendorMemberFactory.create(vendor=vendor, user__preferred_language="it")
    VendorMemberFactory.create(vendor=other, user__preferred_language="xx")
    brand = BrandFactory.create(name="Acmee", status=Brand.Status.PENDING)
    ProductFactory.create(vendor=vendor, brand=brand)
    ProductFactory.create(vendor=other, brand=brand)
    brands.reject_brand(brand, ctx=CTX)
    languages = {n.recipient: n.language for n in _rejected_notices()}
    assert languages[vendor.contact_email] == "it"
    assert languages[other.contact_email] == "en"


def test_the_notice_lists_the_product_name_and_sku_and_the_brand() -> None:
    vendor = VendorFactory.create()
    brand = BrandFactory.create(name="Acmee", status=Brand.Status.PENDING)
    product = ProductFactory.create(vendor=vendor, brand=brand, languages=["en"])
    title = product.translations.get(language="en").title
    brands.reject_brand(brand, ctx=CTX)
    (notice,) = _rejected_notices()
    rendered = render_email(TEMPLATE, notice.language, notice.context_data)
    assert "Acmee" in rendered.text_body
    assert "Acmee" in rendered.html_body
    assert title in rendered.text_body
    assert product.sku in rendered.html_body


def test_a_dry_run_and_a_brand_without_products_send_nothing() -> None:
    vendor = VendorFactory.create()
    brand = BrandFactory.create(status=Brand.Status.PENDING)
    ProductFactory.create(vendor=vendor, brand=brand)
    brands.reject_brand(brand, ctx=CTX, dry_run=True)
    assert not _rejected_notices()
    empty = BrandFactory.create(status=Brand.Status.PENDING)
    brands.reject_brand(empty, ctx=CTX)
    assert not _rejected_notices()


def test_an_approval_sends_no_rejection_notice() -> None:
    brand = BrandFactory.create(status=Brand.Status.PENDING)
    ProductFactory.create(brand=brand)
    brands.approve_brand(brand, ctx=CTX)
    assert not _rejected_notices()


def test_rejecting_a_batch_sends_one_notice_per_vendor_naming_every_brand() -> None:
    vendor = VendorFactory.create()
    outcome = brands.propose_batch(vendor, ["Alpha", "Beta"], source="run", ctx=CTX)
    alpha, beta = outcome.created
    first = ProductFactory.create(vendor=vendor, brand=alpha)
    second = ProductFactory.create(vendor=vendor, brand=beta)
    assert outcome.batch is not None
    brands.reject_batch(outcome.batch, ctx=CTX)
    (notice,) = _rejected_notices()
    text = str(notice.context_data)
    assert "Alpha" in text
    assert "Beta" in text
    assert first.sku in text
    assert second.sku in text


def test_a_rejected_brand_notice_is_not_sent_twice() -> None:
    vendor = VendorFactory.create()
    brand = BrandFactory.create(status=Brand.Status.PENDING)
    ProductFactory.create(vendor=vendor, brand=brand)
    brands.reject_brand(brand, ctx=CTX)
    assert len(_rejected_notices()) == 1


def test_the_title_falls_back_to_the_sku_without_a_translation() -> None:
    vendor = VendorFactory.create()
    brand = BrandFactory.create(status=Brand.Status.PENDING)
    product = ProductFactory.create(vendor=vendor, brand=brand)
    ProductTranslationFactory.create(product=product, language="de", title="Nur Deutsch")
    brands.reject_brand(brand, ctx=CTX)
    (notice,) = _rejected_notices()
    assert product.sku in str(notice.context_data)
