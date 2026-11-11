"""CAT.20 (2/3): vendors pick and propose brands, staff approve, reject or merge them, every
change is audited, and a merged brand's old page redirects."""

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.test.utils import override_settings

from apps.catalog import brands
from apps.catalog.models import Brand, BrandBatch, Product, Redirect
from apps.catalog.tests.factories import (
    BrandFactory,
    BrandTranslationFactory,
    ManufacturerFactory,
    ProductFactory,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.translations import PUBLISHED
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


def _audits(action: str) -> int:
    return AuditLog.objects.filter(action=action).count()


# --- Pick: any approved brand, plus the vendor's own proposals


def test_a_vendor_picks_any_approved_brand_and_its_own_proposals_only() -> None:
    vendor, other = VendorFactory.create(), VendorFactory.create()
    approved = BrandFactory.create()
    mine = brands.propose_brand(vendor, "Mine", ctx=CTX).brand
    theirs = brands.propose_brand(other, "Theirs", ctx=CTX).brand
    rejected = BrandFactory.create(status=Brand.Status.REJECTED)
    choices = set(brands.brand_choices(vendor))
    assert choices == {approved, mine}
    assert theirs not in choices
    assert rejected not in choices


def test_a_vendor_with_50_approved_brands_can_add_products_of_all_of_them() -> None:
    vendor = VendorFactory.create()
    for brand in BrandFactory.create_batch(50):
        ProductFactory.build(vendor=vendor, brand=brand).full_clean()
    assert brands.brand_choices(vendor).count() == 50


def test_a_product_cannot_use_another_vendors_pending_brand() -> None:
    vendor, other = VendorFactory.create(), VendorFactory.create()
    theirs = brands.propose_brand(other, "Theirs", ctx=CTX).brand
    assert theirs is not None
    with pytest.raises(ValidationError) as caught:
        ProductFactory.build(vendor=vendor, brand=theirs).full_clean()
    assert "brand" in caught.value.message_dict
    ProductFactory.build(vendor=other, brand=theirs).full_clean()


def test_a_product_cannot_use_a_rejected_brand() -> None:
    vendor = VendorFactory.create()
    rejected = BrandFactory.create(status=Brand.Status.REJECTED)
    with pytest.raises(ValidationError) as caught:
        ProductFactory.build(vendor=vendor, brand=rejected).full_clean()
    assert "brand" in caught.value.message_dict


# --- Propose


def test_a_proposed_brand_is_pending_not_public_and_audited() -> None:
    vendor = VendorFactory.create()
    outcome = brands.propose_brand(vendor, "Acme", ctx=CTX, wikidata_id="Q42")
    brand = outcome.brand
    assert brand is not None
    assert brand.status == Brand.Status.PENDING
    assert brand.proposed_by == vendor
    assert brand.batch is None
    assert [change.op for change in outcome.changes.changes] == ["create"]
    assert _audits(brands.BRAND_PROPOSED) == 1


def test_a_proposal_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    outcome = brands.propose_brand(vendor, "Acme", ctx=CTX, dry_run=True)
    assert outcome.brand is None
    assert not outcome.changes.is_empty
    assert not Brand.objects.exists()
    assert _audits(brands.BRAND_PROPOSED) == 0


def test_a_proposal_that_duplicates_a_brand_is_refused() -> None:
    BrandFactory.create(name="Acme & Sons", wikidata_id="Q42")
    vendor = VendorFactory.create()
    with pytest.raises(ValidationError):
        brands.propose_brand(vendor, "ACME and sons", ctx=CTX)
    with pytest.raises(ValidationError):
        brands.propose_brand(vendor, "Other", ctx=CTX, wikidata_id="Q42")


def test_a_proposal_with_an_invalid_wikidata_id_or_link_is_refused() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(ValidationError):
        brands.propose_brand(vendor, "A", ctx=CTX, wikidata_id="q42")
    with pytest.raises(ValidationError):
        brands.propose_brand(vendor, "B", ctx=CTX, url="http://example.com")
    with pytest.raises(ValidationError):
        brands.propose_brand(vendor, "C", ctx=CTX, same_as=["http://example.com"])
    assert not Brand.objects.exists()


def test_a_blank_name_is_refused() -> None:
    with pytest.raises(ValidationError):
        brands.propose_brand(VendorFactory.create(), "  ", ctx=CTX)


def test_the_21st_pending_proposal_is_refused_and_approving_frees_slots() -> None:
    vendor = VendorFactory.create()
    created = [brands.propose_brand(vendor, f"Mine {n}", ctx=CTX).brand for n in range(20)]
    with pytest.raises(ValidationError) as caught:
        brands.propose_brand(vendor, "One too many", ctx=CTX)
    assert "20" in str(caught.value)
    assert Brand.objects.count() == 20
    for brand in created[:3]:
        assert brand is not None
        brands.approve_brand(brand, ctx=CTX)
    brands.propose_brand(vendor, "Fits now", ctx=CTX)
    assert brands.pending_proposal_count(vendor) == 18


@override_settings(MPMV_BRAND_PENDING_LIMIT=2)
def test_the_limit_is_a_setting_and_counts_each_vendor_apart() -> None:
    vendor, other = VendorFactory.create(), VendorFactory.create()
    brands.propose_brand(vendor, "A", ctx=CTX)
    brands.propose_brand(vendor, "B", ctx=CTX)
    with pytest.raises(ValidationError):
        brands.propose_brand(vendor, "C", ctx=CTX)
    brands.propose_brand(other, "D", ctx=CTX)


@override_settings(MPMV_BRAND_PENDING_LIMIT=2)
def test_rejecting_frees_a_slot_too() -> None:
    vendor = VendorFactory.create()
    first = brands.propose_brand(vendor, "A", ctx=CTX).brand
    brands.propose_brand(vendor, "B", ctx=CTX)
    assert first is not None
    brands.reject_brand(first, ctx=CTX)
    brands.propose_brand(vendor, "C", ctx=CTX)


# --- Batch proposals from an import


@override_settings(MPMV_BRAND_PENDING_LIMIT=2)
def test_an_import_with_80_new_brands_makes_one_batch_outside_the_limit() -> None:
    vendor = VendorFactory.create()
    names = [f"Imported {n}" for n in range(80)]
    outcome = brands.propose_batch(vendor, names, source="feed import 1", ctx=CTX)
    assert BrandBatch.objects.count() == 1
    assert outcome.batch is not None
    assert len(outcome.created) == 80
    assert Brand.objects.filter(batch=outcome.batch, status=Brand.Status.PENDING).count() == 80
    assert brands.pending_proposal_count(vendor) == 0
    brands.propose_brand(vendor, "Manual 1", ctx=CTX)
    assert _audits(brands.BATCH_PROPOSED) == 1


def test_a_batch_reuses_known_brands_and_dedupes_its_own_names() -> None:
    vendor = VendorFactory.create()
    known = BrandFactory.create(name="Known")
    outcome = brands.propose_batch(
        vendor, ["Known", "KNOWN", "New", "new", "Other"], source="run", ctx=CTX
    )
    assert [brand.name for brand in outcome.created] == ["New", "Other"]
    assert outcome.by_name["KNOWN"] == known
    assert outcome.by_name["new"] == outcome.by_name["New"]


def test_a_batch_with_nothing_new_creates_no_batch() -> None:
    BrandFactory.create(name="Known")
    outcome = brands.propose_batch(VendorFactory.create(), ["Known"], source="run", ctx=CTX)
    assert outcome.batch is None
    assert not BrandBatch.objects.exists()


def test_a_batch_is_approved_or_rejected_together() -> None:
    vendor = VendorFactory.create()
    outcome = brands.propose_batch(vendor, ["A", "B", "C"], source="run", ctx=CTX)
    assert outcome.batch is not None
    brands.approve_batch(outcome.batch, ctx=CTX)
    assert Brand.objects.filter(status=Brand.Status.APPROVED).count() == 3
    outcome = brands.propose_batch(vendor, ["D", "E"], source="run 2", ctx=CTX)
    assert outcome.batch is not None
    brands.reject_batch(outcome.batch, ctx=CTX)
    assert Brand.objects.filter(status=Brand.Status.REJECTED).count() == 2


# --- Review


def test_approving_makes_the_brand_public_and_audits_it(client: Client) -> None:
    vendor = VendorFactory.create()
    brand = brands.propose_brand(vendor, "Acme", ctx=CTX).brand
    assert brand is not None
    BrandTranslationFactory.create(brand=brand, slug="acme", status=PUBLISHED)
    assert client.get("/brand/acme/").status_code == 404
    brands.approve_brand(brand, ctx=CTX)
    brand.refresh_from_db()
    assert brand.status == Brand.Status.APPROVED
    assert client.get("/brand/acme/").status_code == 200
    assert _audits(brands.BRAND_APPROVED) == 1


def test_a_review_dry_run_changes_nothing() -> None:
    brand = brands.propose_brand(VendorFactory.create(), "Acme", ctx=CTX).brand
    assert brand is not None
    changes = brands.approve_brand(brand, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    brand.refresh_from_db()
    assert brand.status == Brand.Status.PENDING
    assert _audits(brands.BRAND_APPROVED) == 0


def test_only_a_pending_brand_is_reviewed() -> None:
    approved = BrandFactory.create()
    with pytest.raises(ValidationError):
        brands.approve_brand(approved, ctx=CTX)
    with pytest.raises(ValidationError):
        brands.reject_brand(approved, ctx=CTX)


def test_rejecting_releases_the_products_of_the_proposing_vendor() -> None:
    vendor = VendorFactory.create()
    brand = brands.propose_brand(vendor, "Acme", ctx=CTX).brand
    product = ProductFactory.create(vendor=vendor, brand=brand)
    brands.reject_brand(brand, ctx=CTX)  # type: ignore[arg-type]
    product.refresh_from_db()
    assert product.brand is None
    assert _audits(brands.BRAND_REJECTED) == 1


# --- Merge


def test_merging_moves_products_deletes_the_brand_and_redirects_its_page(
    client: Client,
) -> None:
    source = BrandFactory.create(name="Acme Old")
    target = BrandFactory.create(name="Acme")
    BrandTranslationFactory.create(brand=source, slug="acme-old", status=PUBLISHED)
    BrandTranslationFactory.create(brand=target, slug="acme", status=PUBLISHED)
    ProductFactory.create_batch(2, brand=source)
    other = ProductFactory.create(brand=target)
    assert client.get("/brand/acme-old/").status_code == 200

    changes = brands.merge_brands(source, target, ctx=CTX)

    assert not Brand.objects.filter(pk=source.pk).exists()
    assert Product.objects.filter(brand=target).count() == 3
    other.refresh_from_db()
    assert other.brand == target
    redirect = Redirect.objects.get(source_path="/brand/acme-old/")
    assert redirect.target_path == "/brand/acme/"
    assert redirect.code == 301
    assert redirect.origin == Redirect.Origin.MERGED_BRAND
    response = client.get("/brand/acme-old/")
    assert response.status_code == 301
    assert response["Location"] == "/brand/acme/"
    assert not changes.is_empty
    # The removed brand, and the target with its new product count.
    assert _audits(brands.BRAND_MERGED) == 2
    assert AuditLog.objects.filter(
        action="catalog.redirect.created", target_key="/brand/acme-old/"
    ).exists()


def test_merging_redirects_every_language_and_keeps_earlier_redirects_one_hop() -> None:
    source = BrandFactory.create(name="Old")
    target = BrandFactory.create(name="New")
    BrandTranslationFactory.create(brand=source, slug="old", language="en", status=PUBLISHED)
    BrandTranslationFactory.create(brand=source, slug="vecchio", language="it", status=PUBLISHED)
    BrandTranslationFactory.create(brand=target, slug="new", language="en", status=PUBLISHED)
    BrandTranslationFactory.create(brand=target, slug="nuovo", language="it", status=PUBLISHED)
    earlier = Redirect.objects.create(
        source_path="/brand/older/",
        code=301,
        target_kind=Redirect.Target.PATH,
        target_path="/brand/old/",
    )
    brands.merge_brands(source, target, ctx=CTX)
    paths = dict(Redirect.objects.values_list("source_path", "target_path"))
    assert paths["/brand/old/"] == "/brand/new/"
    assert paths["/it/marchio/vecchio/"] == "/it/marchio/nuovo/"
    earlier.refresh_from_db()
    assert earlier.target_path == "/brand/new/"


def test_merging_a_pending_brand_makes_no_redirect_and_moves_vendor_products() -> None:
    vendor = VendorFactory.create()
    pending = brands.propose_brand(vendor, "Acmee", ctx=CTX).brand
    target = BrandFactory.create(name="Acme")
    product = ProductFactory.create(vendor=vendor, brand=pending)
    brands.merge_brands(pending, target, ctx=CTX)  # type: ignore[arg-type]
    product.refresh_from_db()
    assert product.brand == target
    assert not Redirect.objects.exists()
    assert brands.pending_proposal_count(vendor) == 0


def test_a_merge_dry_run_changes_nothing() -> None:
    source, target = BrandFactory.create(), BrandFactory.create()
    ProductFactory.create(brand=source)
    changes = brands.merge_brands(source, target, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert Brand.objects.filter(pk=source.pk).exists()
    assert Product.objects.filter(brand=source).count() == 1
    assert _audits(brands.BRAND_MERGED) == 0


def test_a_merge_into_itself_or_into_an_unapproved_brand_is_refused() -> None:
    brand = BrandFactory.create()
    pending = BrandFactory.create(status=Brand.Status.PENDING)
    with pytest.raises(ValidationError):
        brands.merge_brands(brand, brand, ctx=CTX)
    with pytest.raises(ValidationError):
        brands.merge_brands(brand, pending, ctx=CTX)


# --- Staff edits are audited


def test_saving_and_deleting_a_brand_and_a_manufacturer_are_audited() -> None:
    brand = Brand(name="Acme", wikidata_id="Q42")
    brands.save_brand(brand, ctx=CTX)
    brand.url = "https://acme.example/"
    brands.save_brand(brand, ctx=CTX)
    assert _audits(brands.BRAND_CREATED) == 1
    assert _audits(brands.BRAND_UPDATED) == 1
    maker = ManufacturerFactory.build()
    brands.save_manufacturer(maker, ctx=CTX)
    assert _audits(brands.MANUFACTURER_CREATED) == 1
    brands.delete_manufacturer(maker, ctx=CTX)
    assert _audits(brands.MANUFACTURER_DELETED) == 1
    brands.delete_brand(brand, ctx=CTX)
    assert _audits(brands.BRAND_DELETED) == 1


def test_saving_an_invalid_brand_is_refused() -> None:
    with pytest.raises(ValidationError):
        brands.save_brand(Brand(name="Acme", wikidata_id="nope"), ctx=CTX)


def test_a_brand_with_products_is_not_deleted_but_merged() -> None:
    brand = BrandFactory.create()
    ProductFactory.create(brand=brand)
    with pytest.raises(ValidationError):
        brands.delete_brand(brand, ctx=CTX)
    assert Brand.objects.filter(pk=brand.pk).exists()
