"""CMP.3 part 1: GPSR data, per-vendor defaults and the publication block (spec 11 CMP.3).

Done when (this part): a product without GPSR data cannot be published. The catalog publishing
services (CAT.10) ask `apps.catalog.publishing.check_publishable`, where compliance registers the
GPSR check; catalog never imports compliance (spec 01 dependency graph).
"""

import pytest
from django.core.exceptions import ValidationError

from apps.catalog import publishing
from apps.catalog.tests.factories import ProductFactory
from apps.compliance import services
from apps.compliance.models import GpsrInfo, VendorGpsrDefaults
from apps.compliance.tests.factories import GpsrInfoFactory, VendorGpsrDefaultsFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:compliance", source="admin", ip="127.0.0.1")

MAKER = {
    "manufacturer_name": "Example Maker S.r.l.",
    "manufacturer_address": "Via Esempio 1, 00100 Roma",
    "manufacturer_contact": "safety@maker.example",
    "manufacturer_country": "IT",
}
OUTSIDE_EU = {
    "manufacturer_name": "Far Away Works Ltd",
    "manufacturer_address": "1 Example Road, Example City",
    "manufacturer_contact": "https://faraway.example/contact",
    "manufacturer_country": "CN",
}
EU_PERSON = {
    "eu_responsible_name": "EU Rep GmbH",
    "eu_responsible_address": "Beispielstrasse 1, 10115 Berlin",
    "eu_responsible_contact": "rep@eurep.example",
}


# --- Per-vendor defaults


def test_vendor_defaults_dry_run_writes_nothing() -> None:
    vendor = VendorFactory.create()
    changes = services.set_vendor_gpsr_defaults(vendor, MAKER, ctx=CTX, dry_run=True)
    assert [c.op for c in changes.changes] == ["create"]
    assert changes.changes[0].model == "compliance.vendorgpsrdefaults"
    assert not VendorGpsrDefaults.objects.exists()
    assert not AuditLog.objects.exists()


def test_vendor_defaults_are_saved_and_audited_with_the_vendor() -> None:
    vendor = VendorFactory.create()
    services.set_vendor_gpsr_defaults(vendor, MAKER, ctx=CTX)
    defaults = VendorGpsrDefaults.objects.get(vendor=vendor)
    assert defaults.manufacturer_name == "Example Maker S.r.l."
    entry = AuditLog.objects.get(action=services.VENDOR_DEFAULTS_CREATED)
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.after is not None
    assert entry.after["manufacturer_country"] == "IT"


def test_vendor_defaults_update_audits_only_the_change() -> None:
    defaults = VendorGpsrDefaultsFactory.create(**MAKER)
    changes = services.set_vendor_gpsr_defaults(
        defaults.vendor, {"manufacturer_contact": "https://maker.example/safety"}, ctx=CTX
    )
    assert set(changes.changes[0].fields) == {"manufacturer_contact"}
    entry = AuditLog.objects.get(action=services.VENDOR_DEFAULTS_UPDATED)
    assert entry.before is not None
    assert entry.before["manufacturer_contact"] == "safety@maker.example"
    defaults.refresh_from_db()
    assert defaults.manufacturer_name == "Example Maker S.r.l."  # untouched


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manufacturer_contact", "call us"),
        ("eu_responsible_contact", "not-an-address"),
        ("manufacturer_country", "XX"),
    ],
)
def test_invalid_values_are_refused_also_on_dry_run(field: str, value: str) -> None:
    vendor = VendorFactory.create()
    with pytest.raises(ValidationError) as exc:
        services.set_vendor_gpsr_defaults(vendor, {**MAKER, field: value}, ctx=CTX, dry_run=True)
    assert field in exc.value.message_dict


def test_unknown_fields_are_refused() -> None:
    vendor = VendorFactory.create()
    with pytest.raises(ValidationError):
        services.set_vendor_gpsr_defaults(vendor, {"vendor": "other"}, ctx=CTX)


# --- Product GPSR data


def test_product_gpsr_dry_run_writes_nothing() -> None:
    product = ProductFactory.create()
    changes = services.set_product_gpsr(
        product, {**MAKER, "model_number": "M-1"}, ctx=CTX, dry_run=True
    )
    assert [c.op for c in changes.changes] == ["create"]
    assert not GpsrInfo.objects.exists()
    assert not AuditLog.objects.exists()


def test_product_gpsr_is_saved_and_audited() -> None:
    product = ProductFactory.create()
    services.set_product_gpsr(product, {"batch": "LOT-7", **OUTSIDE_EU, **EU_PERSON}, ctx=CTX)
    info = GpsrInfo.objects.get(product=product)
    assert info.batch == "LOT-7"
    assert product.gpsr == info
    entry = AuditLog.objects.get(action=services.PRODUCT_GPSR_CREATED)
    assert entry.vendor_key == str(product.vendor.public_id)
    assert entry.target_key == str(product.public_id)
    assert entry.after is not None
    assert entry.after["product"] == str(product.public_id)

    services.set_product_gpsr(product, {"batch": "LOT-8"}, ctx=CTX)
    updated = AuditLog.objects.get(action=services.PRODUCT_GPSR_UPDATED)
    assert updated.before is not None
    assert updated.after is not None
    assert (updated.before["batch"], updated.after["batch"]) == ("LOT-7", "LOT-8")


# --- Publication block


def test_a_product_without_any_gpsr_data_is_blocked() -> None:
    product = ProductFactory.create(gtin="")
    assert services.gpsr_publication_blockers(product) == (
        "manufacturer_name",
        "manufacturer_address",
        "manufacturer_contact",
        "manufacturer_country",
        "product_identifier",
    )
    with pytest.raises(ValidationError) as exc:
        services.check_gpsr_publishable(product)
    assert exc.value.error_dict["gpsr"][0].code == "gpsr_missing"


def test_vendor_defaults_and_the_product_gtin_are_enough() -> None:
    defaults = VendorGpsrDefaultsFactory.create(**MAKER)
    product = ProductFactory.create(vendor=defaults.vendor, gtin="8001234567890")
    assert services.gpsr_publication_blockers(product) == ()
    services.check_gpsr_publishable(product)  # no error


def test_the_mpn_model_number_or_batch_identify_the_product() -> None:
    defaults = VendorGpsrDefaultsFactory.create(**MAKER)
    by_mpn = ProductFactory.create(vendor=defaults.vendor, gtin="", mpn="MPN-1")
    assert services.gpsr_publication_blockers(by_mpn) == ()
    by_batch = ProductFactory.create(vendor=defaults.vendor, gtin="")
    GpsrInfoFactory.create(product=by_batch, batch="LOT-1")
    assert services.gpsr_publication_blockers(by_batch) == ()


def test_another_vendors_defaults_never_apply() -> None:
    VendorGpsrDefaultsFactory.create(**MAKER)
    product = ProductFactory.create(gtin="8001234567890")
    assert "manufacturer_name" in services.gpsr_publication_blockers(product)


def test_a_non_eu_manufacturer_on_the_product_needs_the_eu_responsible_person() -> None:
    defaults = VendorGpsrDefaultsFactory.create(**MAKER)
    product = ProductFactory.create(vendor=defaults.vendor, gtin="8001234567890")
    GpsrInfoFactory.create(product=product, **OUTSIDE_EU)
    assert services.gpsr_publication_blockers(product) == (
        "eu_responsible_name",
        "eu_responsible_address",
        "eu_responsible_contact",
    )
    services.set_product_gpsr(product, EU_PERSON, ctx=CTX)
    assert services.gpsr_publication_blockers(product) == ()


def test_the_vendor_eu_responsible_person_fills_the_product_block() -> None:
    defaults = VendorGpsrDefaultsFactory.create(**MAKER, **EU_PERSON)
    product = ProductFactory.create(vendor=defaults.vendor, gtin="8001234567890")
    GpsrInfoFactory.create(product=product, **OUTSIDE_EU)
    assert services.gpsr_publication_blockers(product) == ()


def test_the_catalog_publishing_hook_runs_the_gpsr_check() -> None:
    product = ProductFactory.create(gtin="")
    blockers = publishing.publication_blockers(product)
    assert "gpsr" in blockers
    assert len(blockers["gpsr"]) == 5
    with pytest.raises(ValidationError) as exc:
        publishing.check_publishable(product)
    assert "gpsr" in exc.value.message_dict

    services.set_vendor_gpsr_defaults(product.vendor, MAKER, ctx=CTX)
    services.set_product_gpsr(product, {"model_number": "M-1"}, ctx=CTX)
    assert "gpsr" not in publishing.publication_blockers(product)
    publishing.check_publishable(product)


def test_gpsr_data_is_vendor_scoped() -> None:
    mine = GpsrInfoFactory.create()
    GpsrInfoFactory.create()
    assert list(GpsrInfo.objects.for_vendor(mine.product.vendor)) == [mine]
    defaults = VendorGpsrDefaultsFactory.create()
    VendorGpsrDefaultsFactory.create()
    assert list(VendorGpsrDefaults.objects.for_vendor(defaults.vendor)) == [defaults]
