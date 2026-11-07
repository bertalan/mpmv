"""SHP.1: staff admin for the shipping configuration; every change is audited."""

from collections.abc import Callable
from typing import Any

import pytest
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.shipping import engine
from apps.shipping.models import Carrier, ShippingZone, VendorZoneRate, WeightBand
from apps.shipping.tests.factories import (
    CarrierFactory,
    ShippingConfigVersionFactory,
    ShippingZoneFactory,
    VendorZoneRateFactory,
    WeightBandFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db


def domain_entries() -> "QuerySet[AuditLog]":
    """Audit entries of this app: signing in staff writes a login entry too (SEC.4)."""
    return AuditLog.objects.filter(action__startswith="shipping.")


MfaLogin = Callable[[Client, User], Client]


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def bands_form(*bands: tuple[str, str, str], initial: int = 0) -> dict[str, Any]:
    data: dict[str, Any] = {
        "bands-TOTAL_FORMS": str(len(bands)),
        "bands-INITIAL_FORMS": str(initial),
        "bands-MIN_NUM_FORMS": "0",
        "bands-MAX_NUM_FORMS": "1000",
    }
    for i, (from_kg, to_kg, cost) in enumerate(bands):
        data.update(
            {
                f"bands-{i}-from_kg": from_kg,
                f"bands-{i}-to_kg": to_kg,
                f"bands-{i}-cost_net": cost,
                f"bands-{i}-surcharge_net": "0.00",
            }
        )
    return data


def rate_form(vendor: Any, zone: Any, carrier: Any, *bands: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "vendor": str(vendor.pk),
        "zone": str(zone.pk),
        "carrier": str(carrier.pk),
        "over_max_weight": "",
        **bands_form(*bands),
    }


def rate_parts() -> tuple[Vendor, ShippingZone, Carrier]:
    return VendorFactory.create(), ShippingZoneFactory.create(), CarrierFactory.create()


@pytest.mark.parametrize(
    "model", ["carrier", "shippingzone", "vendorzonerate", "pickupoption", "shippingconfigversion"]
)
def test_changelists_load(staff_client: Client, model: str) -> None:
    VendorZoneRateFactory.create()
    ShippingConfigVersionFactory.create()
    response = staff_client.get(reverse(f"admin:shipping_{model}_changelist"))
    assert response.status_code == 200


def test_add_zone_normalizes_countries_and_is_audited(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:shipping_shippingzone_add"),
        {
            "code": "gr-islands",
            "name": "GR remote islands",
            "priority": "10",
            "countries": "el",
            "postcode_patterns": "70000-74999,846*",
            "states": "",
        },
    )
    assert response.status_code == 302, response.content.decode()
    zone = ShippingZone.objects.get()
    assert zone.countries == ["GR"]
    assert zone.postcode_patterns == ["70000-74999", "846*"]
    entry = AuditLog.objects.get(action="shipping.shippingzone.created")
    assert entry.source == "admin"
    assert entry.actor_label == "staff:staff@example.com"
    assert entry.target_key == str(zone.public_id)
    assert entry.after is not None
    assert entry.after["countries"] == ["GR"]


def test_add_rate_with_adjacent_bands(staff_client: Client) -> None:
    vendor, zone, carrier = rate_parts()
    data = rate_form(vendor, zone, carrier, ("0", "5", "9.90"), ("5", "", "14.90"))
    response = staff_client.post(reverse("admin:shipping_vendorzonerate_add"), data)
    assert response.status_code == 302, response.content.decode()
    rate = VendorZoneRate.objects.get()
    assert rate.over_max_weight == engine.PLATFORM_DEFAULT == ""
    assert [(b.from_kg, b.to_kg) for b in rate.bands.order_by("from_kg")] == [
        (0, 5),
        (5, None),
    ]
    assert AuditLog.objects.filter(action="shipping.vendorzonerate.created").count() == 1
    assert AuditLog.objects.filter(action="shipping.weightband.created").count() == 2


def test_overlapping_bands_in_one_submission_are_refused_with_a_form_error(
    staff_client: Client,
) -> None:
    vendor, zone, carrier = rate_parts()
    data = rate_form(vendor, zone, carrier, ("0", "5", "9.90"), ("4", "10", "14.90"))
    response = staff_client.post(reverse("admin:shipping_vendorzonerate_add"), data)
    assert response.status_code == 200
    assert "Weight bands overlap" in response.content.decode()
    assert not VendorZoneRate.objects.exists()
    assert not domain_entries().exists()


def test_a_second_carrier_for_the_same_vendor_and_zone_is_a_form_error(
    staff_client: Client,
) -> None:
    existing = VendorZoneRateFactory.create()
    data = rate_form(existing.vendor, existing.zone, CarrierFactory.create())
    response = staff_client.post(reverse("admin:shipping_vendorzonerate_add"), data)
    assert response.status_code == 200
    assert VendorZoneRate.objects.count() == 1


def test_changing_a_band_is_audited_with_before_and_after(staff_client: Client) -> None:
    band = WeightBandFactory.create(cost_net="9.90")
    rate = band.rate
    data = rate_form(rate.vendor, rate.zone, rate.carrier, ("0.000", "5.000", "11.50"))
    data["bands-INITIAL_FORMS"] = "1"
    data["bands-0-id"] = str(band.pk)
    data["bands-0-rate"] = str(rate.pk)
    response = staff_client.post(
        reverse("admin:shipping_vendorzonerate_change", args=[rate.pk]), data
    )
    assert response.status_code == 302, response.content.decode()
    band.refresh_from_db()
    assert str(band.cost_net) == "11.50"
    entry = AuditLog.objects.get(action="shipping.weightband.updated")
    assert entry.before is not None
    assert entry.before["cost_net"] == "9.90"
    assert entry.after is not None
    assert entry.after["cost_net"] == "11.50"
    assert entry.vendor_key == str(rate.vendor.public_id)
    assert not AuditLog.objects.filter(action="shipping.vendorzonerate.updated").exists()


def test_deleting_a_rate_is_audited_with_its_bands(staff_client: Client) -> None:
    rate = WeightBandFactory.create().rate
    response = staff_client.post(
        reverse("admin:shipping_vendorzonerate_delete", args=[rate.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert not VendorZoneRate.objects.exists()
    entry = AuditLog.objects.get(action="shipping.vendorzonerate.deleted")
    assert entry.target_key == str(rate.public_id)
    assert entry.after is None
    assert AuditLog.objects.filter(action="shipping.weightband.deleted").count() == 1


def test_deleting_a_band_inline_is_audited(staff_client: Client) -> None:
    band = WeightBandFactory.create()
    rate = band.rate
    data = rate_form(rate.vendor, rate.zone, rate.carrier, ("0.000", "5.000", "9.90"))
    data.update({"bands-INITIAL_FORMS": "1", "bands-0-id": str(band.pk), "bands-0-DELETE": "on"})
    data["bands-0-rate"] = str(rate.pk)
    response = staff_client.post(
        reverse("admin:shipping_vendorzonerate_change", args=[rate.pk]), data
    )
    assert response.status_code == 302, response.content.decode()
    assert not WeightBand.objects.exists()
    assert AuditLog.objects.filter(action="shipping.weightband.deleted").count() == 1


def test_bulk_deleting_zones_audits_each_one(staff_client: Client) -> None:
    zones = ShippingZoneFactory.create_batch(2)
    response = staff_client.post(
        reverse("admin:shipping_shippingzone_changelist"),
        {"action": "delete_selected", "_selected_action": [z.pk for z in zones], "post": "yes"},
    )
    assert response.status_code == 302
    assert not ShippingZone.objects.exists()
    deleted = AuditLog.objects.filter(action="shipping.shippingzone.deleted")
    assert sorted(e.target_key for e in deleted) == sorted(str(z.public_id) for z in zones)


def test_config_versions_are_read_only_in_the_admin(staff_client: Client) -> None:
    version = ShippingConfigVersionFactory.create()
    assert staff_client.get(reverse("admin:shipping_shippingconfigversion_add")).status_code == 403
    change_url = reverse("admin:shipping_shippingconfigversion_change", args=[version.pk])
    assert staff_client.get(change_url).status_code == 200  # view only
    assert staff_client.post(change_url, {"note": "edited"}).status_code == 403
    delete_url = reverse("admin:shipping_shippingconfigversion_delete", args=[version.pk])
    assert staff_client.post(delete_url, {"post": "yes"}).status_code == 403
