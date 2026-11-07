"""SHP.1 follow-up (coherence check 4, finding 1): shipping configuration writes go through
audited services with dry-run and a ChangeSet, whatever the adapter (admin, SHP.5 import,
SHP.6 editor)."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.shipping.models import ShippingZone, VendorZoneRate, WeightBand
from apps.shipping.services import config_snapshot, delete_config, save_config
from apps.shipping.tests.factories import (
    ShippingZoneFactory,
    VendorZoneRateFactory,
    WeightBandFactory,
)
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:shipping", source="admin", ip="127.0.0.1")


def new_zone() -> ShippingZone:
    return ShippingZone(code="gr-islands", name="GR islands", priority=10, countries=["GR"])


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
    assert entry.after["rate"] == str(band.rate.public_id)
    assert entry.vendor_key == str(band.rate.vendor.public_id)


def test_a_dry_run_change_is_computed_against_the_database() -> None:
    band = WeightBandFactory.create(cost_net=Decimal("9.90"))
    band.cost_net = Decimal("11.50")
    changes = save_config(band, ctx=CTX, dry_run=True)
    assert changes.changes[0].fields == {"cost_net": (Decimal("9.90"), Decimal("11.50"))}
    assert WeightBand.objects.get().cost_net == Decimal("9.90")
    assert not AuditLog.objects.exists()


def test_saving_without_changes_is_not_audited() -> None:
    rate = VendorZoneRateFactory.create()
    assert save_config(rate, ctx=CTX).is_empty
    assert not AuditLog.objects.exists()


def test_an_overlapping_band_is_refused_even_on_dry_run() -> None:
    band = WeightBandFactory.create(from_kg=Decimal("0"), to_kg=Decimal("5"))
    overlapping = WeightBand(
        rate=band.rate, from_kg=Decimal("4"), to_kg=Decimal("10"), cost_net=Decimal("1.00")
    )
    with pytest.raises(ValidationError):
        save_config(overlapping, ctx=CTX, dry_run=True)
    with pytest.raises(ValidationError):
        save_config(overlapping, ctx=CTX)
    assert WeightBand.objects.count() == 1
    assert not AuditLog.objects.exists()


def test_a_negative_amount_is_refused() -> None:
    band = WeightBandFactory.build(rate=VendorZoneRateFactory.create(), cost_net=Decimal("-1"))
    with pytest.raises(ValidationError):
        save_config(band, ctx=CTX)
    assert not WeightBand.objects.exists()


def test_deleting_a_rate_audits_the_rate_and_its_bands() -> None:
    band = WeightBandFactory.create()
    rate = band.rate
    preview = delete_config([rate], ctx=CTX, dry_run=True)
    assert preview.summary() == {"create": 0, "update": 0, "delete": 2}
    assert VendorZoneRate.objects.exists()
    assert not AuditLog.objects.exists()

    delete_config([rate], ctx=CTX)
    assert not VendorZoneRate.objects.exists()
    assert not WeightBand.objects.exists()
    actions = sorted(AuditLog.objects.values_list("action", "target_key"))
    assert actions == sorted(
        [
            ("shipping.vendorzonerate.deleted", str(rate.public_id)),
            ("shipping.weightband.deleted", str(band.public_id)),
        ]
    )
    assert all(e.after is None and e.before for e in AuditLog.objects.all())


def test_deleting_a_band_and_its_rate_together_audits_the_band_once() -> None:
    band = WeightBandFactory.create()
    delete_config([band.rate, band], ctx=CTX)
    assert AuditLog.objects.filter(action="shipping.weightband.deleted").count() == 1


def test_only_audited_shipping_models_are_accepted() -> None:
    with pytest.raises(TypeError):
        save_config(VendorFactory.build(), ctx=CTX)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        delete_config([VendorFactory.create()], ctx=CTX)  # type: ignore[list-item]


def test_the_snapshot_has_no_database_ids() -> None:
    rate = VendorZoneRateFactory.create()
    snapshot = config_snapshot(rate)
    assert "id" not in snapshot
    assert snapshot["vendor"] == str(rate.vendor.public_id)
    assert snapshot["zone"] == str(rate.zone.public_id)
    assert snapshot["carrier"] == str(rate.carrier.public_id)
    assert config_snapshot(ShippingZoneFactory.create())["countries"] == ["IT"]
