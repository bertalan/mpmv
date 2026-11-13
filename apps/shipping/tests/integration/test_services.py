"""SHP.1 and SHP.12: shipping configuration writes go through audited services with dry-run and
a ChangeSet, whatever the adapter (admin, SHP.5 import, SHP.6 editor); zone snapshots feed the
pure zone resolution."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.shipping import engine
from apps.shipping.engine import Destination
from apps.shipping.models import CountryGroup, ShippingMethod, ShippingZone, WeightBand
from apps.shipping.services import (
    config_snapshot,
    delete_config,
    save_config,
    zone_snaps,
    zones_available_to,
)
from apps.shipping.tests.factories import (
    CarrierFactory,
    CountryGroupFactory,
    PickupOptionFactory,
    ShippingMethodFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:shipping", source="admin", ip="127.0.0.1")


def new_zone() -> ShippingZone:
    return ShippingZone(code="gr-islands", name="GR islands", priority=10, countries=["GR"])


def zone_for(vendor: Vendor, dest: Destination) -> str | None:
    found = engine.resolve_zone(zone_snaps(vendor), vendor.pk, dest)
    return None if found is None else found.code


def test_a_dry_run_create_returns_the_change_and_writes_nothing() -> None:
    zone = new_zone()
    changes = save_config(zone, ctx=CTX, dry_run=True)
    assert changes.summary() == {"create": 1, "update": 0, "delete": 0}
    change = changes.changes[0]
    assert (change.model, change.key) == ("shipping.shippingzone", str(zone.public_id))
    assert change.after is not None
    assert change.after["countries"] == ["GR"]
    assert not ShippingZone.objects.exists()
    assert not AuditLog.objects.exists()


def test_a_create_is_saved_and_audited() -> None:
    zone = new_zone()
    save_config(zone, ctx=CTX)
    assert ShippingZone.objects.get().pk == zone.pk
    entry = AuditLog.objects.get()
    assert entry.action == "shipping.shippingzone.created"
    assert entry.target_key == str(zone.public_id)
    assert entry.actor_label == "staff:shipping"
    assert entry.before is None
    assert entry.vendor_key == ""  # a platform zone


def test_a_vendor_zone_is_audited_for_its_vendor() -> None:
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.build(vendor=vendor, postcode_patterns=["07*"])
    save_config(zone, ctx=CTX)
    entry = AuditLog.objects.get()
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.after is not None
    assert entry.after["postcode_patterns"] == ["07*"]


def test_a_method_create_is_audited_with_its_vat_treatment() -> None:
    method = ShippingMethodFactory.build(
        vendor=VendorFactory.create(),
        zone=ShippingZoneFactory.create(),
        carrier=CarrierFactory.create(),
        tax_treatment=engine.FOLLOWS_GOODS,
        free_shipping_threshold=Decimal("49.00"),
    )
    preview = save_config(method, ctx=CTX, dry_run=True)
    assert preview.changes[0].after is not None
    assert preview.changes[0].after["tax_treatment"] == "follows_goods"
    assert not AuditLog.objects.exists()
    save_config(method, ctx=CTX)
    entry = AuditLog.objects.get(action="shipping.shippingmethod.created")
    assert entry.after is not None
    assert entry.after["free_shipping_threshold"] == "49.00"
    assert entry.vendor_key == str(method.vendor.public_id)


def test_a_method_on_another_vendors_zone_is_refused() -> None:
    foreign_zone = ShippingZoneFactory.create(vendor_zone=True)
    method = ShippingMethodFactory.build(
        vendor=VendorFactory.create(), zone=foreign_zone, carrier=CarrierFactory.create()
    )
    with pytest.raises(ValidationError):
        save_config(method, ctx=CTX, dry_run=True)
    assert not ShippingMethod.objects.filter(zone=foreign_zone).exists()


def test_a_band_change_is_audited_with_before_after_and_its_vendor() -> None:
    band = WeightBandFactory.create(cost_net=Decimal("9.90"))
    band.cost_net = Decimal("11.50")
    save_config(band, ctx=CTX)
    band.refresh_from_db()
    assert band.cost_net == Decimal("11.50")
    entry = AuditLog.objects.get()
    assert entry.action == "shipping.weightband.updated"
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["cost_net"], entry.after["cost_net"]) == ("9.90", "11.50")
    # Snapshots name related rows by public id, and the vendor fills the audit vendor filter.
    assert entry.after["method"] == str(band.method.public_id)
    assert entry.vendor_key == str(band.method.vendor.public_id)


def test_a_dry_run_change_is_computed_against_the_database() -> None:
    band = WeightBandFactory.create(cost_net=Decimal("9.90"))
    band.cost_net = Decimal("11.50")
    changes = save_config(band, ctx=CTX, dry_run=True)
    assert changes.changes[0].fields == {"cost_net": (Decimal("9.90"), Decimal("11.50"))}
    assert WeightBand.objects.get().cost_net == Decimal("9.90")
    assert not AuditLog.objects.exists()


def test_saving_without_changes_is_not_audited() -> None:
    method = ShippingMethodFactory.create()
    assert save_config(method, ctx=CTX).is_empty
    assert not AuditLog.objects.exists()


def test_an_overlapping_band_is_refused_even_on_dry_run() -> None:
    band = WeightBandFactory.create(from_kg=Decimal("0"), to_kg=Decimal("5"))
    overlapping = WeightBand(
        method=band.method, from_kg=Decimal("4"), to_kg=Decimal("10"), cost_net=Decimal("1.00")
    )
    with pytest.raises(ValidationError):
        save_config(overlapping, ctx=CTX, dry_run=True)
    with pytest.raises(ValidationError):
        save_config(overlapping, ctx=CTX)
    assert WeightBand.objects.count() == 1
    assert not AuditLog.objects.exists()


def test_a_second_default_method_is_refused_by_validation() -> None:
    default = ShippingMethodFactory.create(default=True)
    second = ShippingMethodFactory.build(
        vendor=default.vendor, zone=default.zone, carrier=default.carrier, is_default=True
    )
    with pytest.raises(ValidationError):
        save_config(second, ctx=CTX, dry_run=True)


def test_a_negative_amount_is_refused() -> None:
    band = WeightBandFactory.build(method=ShippingMethodFactory.create(), cost_net=Decimal("-1"))
    with pytest.raises(ValidationError):
        save_config(band, ctx=CTX)
    assert not WeightBand.objects.exists()


def test_deleting_a_method_audits_the_method_and_its_bands() -> None:
    band = WeightBandFactory.create()
    method = band.method
    preview = delete_config([method], ctx=CTX, dry_run=True)
    assert preview.summary() == {"create": 0, "update": 0, "delete": 2}
    assert ShippingMethod.objects.exists()
    assert not AuditLog.objects.exists()

    delete_config([method], ctx=CTX)
    assert not ShippingMethod.objects.exists()
    assert not WeightBand.objects.exists()
    actions = sorted(AuditLog.objects.values_list("action", "target_key"))
    assert actions == sorted(
        [
            ("shipping.shippingmethod.deleted", str(method.public_id)),
            ("shipping.weightband.deleted", str(band.public_id)),
        ]
    )
    assert all(e.after is None and e.before for e in AuditLog.objects.all())


def test_deleting_a_band_and_its_method_together_audits_the_band_once() -> None:
    band = WeightBandFactory.create()
    delete_config([band.method, band], ctx=CTX)
    assert AuditLog.objects.filter(action="shipping.weightband.deleted").count() == 1


# --- Country groups (staff-edited, audited)


def test_a_country_group_change_is_audited() -> None:
    group = CountryGroupFactory.build(code="dach", countries=["de", "at", "ch"])
    save_config(group, ctx=CTX)
    group.countries = [*group.countries, "LI"]
    save_config(group, ctx=CTX)
    created, updated = AuditLog.objects.order_by("id")
    assert created.action == "shipping.countrygroup.created"
    assert updated.action == "shipping.countrygroup.updated"
    assert updated.after is not None
    assert updated.after["countries"] == ["DE", "AT", "CH", "LI"]
    assert updated.vendor_key == ""


def test_a_country_group_used_by_a_zone_cannot_be_deleted() -> None:
    group = CountryGroupFactory.create()
    zone = ShippingZoneFactory.create(country_groups=[group.code])
    with pytest.raises(ProtectedError):
        delete_config([group], ctx=CTX, dry_run=True)
    with pytest.raises(ProtectedError):
        delete_config([group], ctx=CTX)
    assert CountryGroup.objects.filter(pk=group.pk).exists()
    assert not AuditLog.objects.exists()
    # Deleting the zone with it is fine: nothing else uses the group.
    delete_config([group, zone], ctx=CTX)
    assert not CountryGroup.objects.filter(pk=group.pk).exists()
    assert AuditLog.objects.filter(action="shipping.countrygroup.deleted").count() == 1


def test_only_audited_shipping_models_are_accepted() -> None:
    with pytest.raises(TypeError):
        save_config(VendorFactory.build(), ctx=CTX)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        delete_config([VendorFactory.create()], ctx=CTX)  # type: ignore[list-item]


def test_the_snapshot_has_no_database_ids() -> None:
    method = ShippingMethodFactory.create()
    snapshot = config_snapshot(method)
    assert "id" not in snapshot
    assert snapshot["vendor"] == str(method.vendor.public_id)
    assert snapshot["zone"] == str(method.zone.public_id)
    assert snapshot["carrier"] == str(method.carrier.public_id)
    assert snapshot["tax_treatment"] == "follows_goods"
    assert config_snapshot(ShippingZoneFactory.create())["countries"] == ["IT"]


# --- Zones available to a vendor, and their snapshots for the pure resolution


def test_a_vendor_sees_platform_zones_and_its_own_zones_only() -> None:
    vendor = VendorFactory.create()
    platform = ShippingZoneFactory.create()
    own = ShippingZoneFactory.create(vendor=vendor)
    ShippingZoneFactory.create(vendor_zone=True)  # another vendor's
    assert set(zones_available_to(vendor)) == {platform, own}


def test_zone_snapshots_cover_the_zones_a_vendor_ships_to_with_groups_expanded() -> None:
    vendor = VendorFactory.create()
    group = CountryGroupFactory.create(countries=["DE", "AT"])
    europe = ShippingZoneFactory.create(countries=[], country_groups=[group.code], priority=50)
    ShippingMethodFactory.create(vendor=vendor, zone=europe)
    ShippingMethodFactory.create(vendor=vendor, zone=europe)  # a second method: one snapshot
    ShippingZoneFactory.create()  # no method of this vendor: not a candidate
    (snap,) = zone_snaps(vendor)
    assert (snap.code, snap.priority, snap.vendor_id) == (europe.code, 50, None)
    assert snap.countries == frozenset()
    assert snap.group_countries == frozenset({"DE", "AT"})


def test_zones_served_only_by_inactive_carriers_are_not_candidates() -> None:
    # An inactive carrier offers no rates (spec 06 §6.3 case 10): its zone must not win the
    # resolution over a zone the vendor can actually ship to.
    vendor = VendorFactory.create()
    sardinia = ShippingZoneFactory.create(
        code="sardinia", postcode_patterns=["07000..09999"], priority=10
    )
    italy = ShippingZoneFactory.create(code="italy", priority=50)
    stopped = CarrierFactory.create(is_active=False)
    ShippingMethodFactory.create(vendor=vendor, zone=sardinia, carrier=stopped)
    ShippingMethodFactory.create(vendor=vendor, zone=italy)
    mixed = ShippingZoneFactory.create(code="mixed", countries=["FR"])
    ShippingMethodFactory.create(vendor=vendor, zone=mixed, carrier=stopped)
    ShippingMethodFactory.create(vendor=vendor, zone=mixed, key="express")
    assert {snap.code for snap in zone_snaps(vendor)} == {"italy", "mixed"}
    assert zone_for(vendor, Destination("IT", postcode="07100")) == italy.code


def test_a_vendor_is_deleted_once_its_shipping_configuration_is() -> None:
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(vendor=vendor)
    method = ShippingMethodFactory.create(vendor=vendor, zone=zone)
    band = WeightBandFactory.create(method=method)
    pickup = PickupOptionFactory.create(vendor=vendor)
    with pytest.raises(ProtectedError), transaction.atomic():
        vendor.delete()
    delete_config([method, pickup, zone], ctx=CTX)
    deleted = set(
        AuditLog.objects.filter(action__endswith=".deleted").values_list("target_key", flat=True)
    )
    assert {str(row.public_id) for row in (zone, method, band, pickup)} <= deleted
    vendor.delete()
    assert not Vendor.objects.filter(pk=vendor.pk).exists()


def test_a_vendor_zone_overrides_the_platform_zone_for_matching_addresses() -> None:
    # SHP.12 "Done when", on stored configuration: vendor A ships to the platform Italy zone
    # and to its own Sardinia zone; vendor B only to the platform zone.
    italy = ShippingZoneFactory.create(code="italy", countries=["IT"], priority=1)
    vendor_a, vendor_b = VendorFactory.create_batch(2)
    sardinia = ShippingZoneFactory.create(
        vendor=vendor_a, code="sardinia", postcode_patterns=["07000..09999"], priority=500
    )
    ShippingMethodFactory.create(vendor=vendor_a, zone=italy, default=True)
    ShippingMethodFactory.create(vendor=vendor_a, zone=sardinia, default=True)
    ShippingMethodFactory.create(vendor=vendor_b, zone=italy, default=True)

    assert zone_for(vendor_a, Destination("IT", postcode="07100")) == sardinia.code
    assert zone_for(vendor_a, Destination("IT", postcode="20100")) == italy.code
    assert zone_for(vendor_b, Destination("IT", postcode="07100")) == italy.code
