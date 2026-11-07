"""SHP.1 and SHP.12: staff admin for the shipping configuration; every change is audited."""

from collections.abc import Callable
from typing import Any

import pytest
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.shipping import engine
from apps.shipping.models import Carrier, CountryGroup, ShippingMethod, ShippingZone, WeightBand
from apps.shipping.tests.factories import (
    CarrierFactory,
    CountryGroupFactory,
    ShippingConfigVersionFactory,
    ShippingMethodFactory,
    ShippingZoneFactory,
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


def inline_management(prefix: str, total: int, initial: int = 0) -> dict[str, str]:
    return {
        f"{prefix}-TOTAL_FORMS": str(total),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }


def bands_form(*bands: tuple[str, str, str], initial: int = 0) -> dict[str, Any]:
    data: dict[str, Any] = inline_management("bands", len(bands), initial)
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


def method_form(
    vendor: Any,
    zone: Any,
    carrier: Any,
    *bands: tuple[str, str, str],
    key: str = "standard",
    is_default: bool = True,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "vendor": str(vendor.pk),
        "zone": str(zone.pk),
        "carrier": str(carrier.pk),
        "key": key,
        "service_level": engine.STANDARD,
        "min_delivery_days": "2",
        "max_delivery_days": "4",
        "free_shipping_threshold": "",
        "tax_treatment": engine.FOLLOWS_GOODS,
        "over_max_weight": "",
        **inline_management("translations", 0),
        **bands_form(*bands),
    }
    if is_default:
        data["is_default"] = "on"
    return data


def zone_form(**fields: str) -> dict[str, str]:
    return {
        "vendor": "",
        "code": "gr-islands",
        "name": "GR remote islands",
        "priority": "10",
        "countries": "el",
        "country_groups": "",
        "postcode_patterns": "70000..74999,846*",
        "states": "",
        **fields,
    }


def method_parts() -> tuple[Vendor, ShippingZone, Carrier]:
    return VendorFactory.create(), ShippingZoneFactory.create(), CarrierFactory.create()


@pytest.mark.parametrize(
    "model",
    [
        "carrier",
        "countrygroup",
        "shippingzone",
        "shippingmethod",
        "pickupoption",
        "shippingconfigversion",
    ],
)
def test_changelists_load(staff_client: Client, model: str) -> None:
    ShippingMethodFactory.create(zone=ShippingZoneFactory.create(vendor_zone=True))
    CountryGroupFactory.create()
    ShippingConfigVersionFactory.create()
    response = staff_client.get(reverse(f"admin:shipping_{model}_changelist"))
    assert response.status_code == 200


def test_add_zone_normalizes_countries_and_is_audited(staff_client: Client) -> None:
    response = staff_client.post(reverse("admin:shipping_shippingzone_add"), zone_form())
    assert response.status_code == 302, response.content.decode()
    zone = ShippingZone.objects.get()
    assert zone.countries == ["GR"]
    assert zone.postcode_patterns == ["70000..74999", "846*"]
    assert zone.vendor is None
    entry = AuditLog.objects.get(action="shipping.shippingzone.created")
    assert entry.source == "admin"
    assert entry.actor_label == "staff:staff@example.com"
    assert entry.target_key == str(zone.public_id)
    assert entry.after is not None
    assert entry.after["countries"] == ["GR"]


def test_the_old_range_syntax_is_a_form_error(staff_client: Client) -> None:
    data = zone_form(postcode_patterns="70000-74999")
    response = staff_client.post(reverse("admin:shipping_shippingzone_add"), data)
    assert response.status_code == 200
    assert "70000..74999" in response.content.decode()
    assert not ShippingZone.objects.exists()


def test_add_vendor_zone_from_country_groups(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    group = CountryGroupFactory.create(code="dach", countries=["DE", "AT", "CH"])
    data = zone_form(
        vendor=str(vendor.pk), code="dach", countries="", country_groups="dach", postcode_patterns=""
    )
    response = staff_client.post(reverse("admin:shipping_shippingzone_add"), data)
    assert response.status_code == 302, response.content.decode()
    zone = ShippingZone.objects.get()
    assert (zone.vendor, zone.country_groups) == (vendor, [group.code])
    entry = AuditLog.objects.get(action="shipping.shippingzone.created")
    assert entry.vendor_key == str(vendor.public_id)


def test_add_method_with_adjacent_bands(staff_client: Client) -> None:
    vendor, zone, carrier = method_parts()
    data = method_form(vendor, zone, carrier, ("0", "5", "9.90"), ("5", "", "14.90"))
    response = staff_client.post(reverse("admin:shipping_shippingmethod_add"), data)
    assert response.status_code == 302, response.content.decode()
    method = ShippingMethod.objects.get()
    assert method.over_max_weight == engine.PLATFORM_DEFAULT == ""
    assert (method.is_default, method.tax_treatment) == (True, engine.FOLLOWS_GOODS)
    assert [(b.from_kg, b.to_kg) for b in method.bands.order_by("from_kg")] == [
        (0, 5),
        (5, None),
    ]
    assert AuditLog.objects.filter(action="shipping.shippingmethod.created").count() == 1
    assert AuditLog.objects.filter(action="shipping.weightband.created").count() == 2


def test_a_second_method_to_the_same_zone_is_accepted(staff_client: Client) -> None:
    existing = ShippingMethodFactory.create(default=True, key="standard")
    data = method_form(
        existing.vendor,
        existing.zone,
        CarrierFactory.create(),
        ("0", "3", "19.90"),
        key="express",
        is_default=False,
    )
    response = staff_client.post(reverse("admin:shipping_shippingmethod_add"), data)
    assert response.status_code == 302, response.content.decode()
    assert ShippingMethod.objects.filter(vendor=existing.vendor, zone=existing.zone).count() == 2


def test_overlapping_bands_in_one_submission_are_refused_with_a_form_error(
    staff_client: Client,
) -> None:
    vendor, zone, carrier = method_parts()
    data = method_form(vendor, zone, carrier, ("0", "5", "9.90"), ("4", "10", "14.90"))
    response = staff_client.post(reverse("admin:shipping_shippingmethod_add"), data)
    assert response.status_code == 200
    assert "Weight bands overlap" in response.content.decode()
    assert not ShippingMethod.objects.exists()
    assert not domain_entries().exists()


def test_a_second_default_for_the_same_vendor_and_zone_is_a_form_error(
    staff_client: Client,
) -> None:
    existing = ShippingMethodFactory.create(default=True)
    data = method_form(existing.vendor, existing.zone, CarrierFactory.create(), key="express")
    response = staff_client.post(reverse("admin:shipping_shippingmethod_add"), data)
    assert response.status_code == 200
    assert ShippingMethod.objects.count() == 1


def test_changing_a_band_is_audited_with_before_and_after(staff_client: Client) -> None:
    band = WeightBandFactory.create(cost_net="9.90")
    method = band.method
    data = method_form(
        method.vendor,
        method.zone,
        method.carrier,
        ("0.000", "5.000", "11.50"),
        key=method.key,
        is_default=False,
    )
    data.update(
        {
            "min_delivery_days": str(method.min_delivery_days),
            "max_delivery_days": str(method.max_delivery_days),
            "bands-INITIAL_FORMS": "1",
            "bands-0-id": str(band.pk),
            "bands-0-method": str(method.pk),
        }
    )
    response = staff_client.post(
        reverse("admin:shipping_shippingmethod_change", args=[method.pk]), data
    )
    assert response.status_code == 302, response.content.decode()
    band.refresh_from_db()
    assert str(band.cost_net) == "11.50"
    entry = AuditLog.objects.get(action="shipping.weightband.updated")
    assert entry.before is not None
    assert entry.before["cost_net"] == "9.90"
    assert entry.after is not None
    assert entry.after["cost_net"] == "11.50"
    assert entry.vendor_key == str(method.vendor.public_id)
    assert not AuditLog.objects.filter(action="shipping.shippingmethod.updated").exists()


def test_deleting_a_method_is_audited_with_its_bands(staff_client: Client) -> None:
    method = WeightBandFactory.create().method
    response = staff_client.post(
        reverse("admin:shipping_shippingmethod_delete", args=[method.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert not ShippingMethod.objects.exists()
    entry = AuditLog.objects.get(action="shipping.shippingmethod.deleted")
    assert entry.target_key == str(method.public_id)
    assert entry.after is None
    assert AuditLog.objects.filter(action="shipping.weightband.deleted").count() == 1


def test_deleting_a_band_inline_is_audited(staff_client: Client) -> None:
    band = WeightBandFactory.create()
    method = band.method
    data = method_form(
        method.vendor,
        method.zone,
        method.carrier,
        ("0.000", "5.000", "9.90"),
        key=method.key,
        is_default=False,
    )
    data.update(
        {
            "min_delivery_days": str(method.min_delivery_days),
            "max_delivery_days": str(method.max_delivery_days),
            "bands-INITIAL_FORMS": "1",
            "bands-0-id": str(band.pk),
            "bands-0-DELETE": "on",
            "bands-0-method": str(method.pk),
        }
    )
    response = staff_client.post(
        reverse("admin:shipping_shippingmethod_change", args=[method.pk]), data
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


def test_add_country_group_is_audited(staff_client: Client) -> None:
    data = {"code": "dach", "countries": "de,at,ch", **inline_management("translations", 0)}
    response = staff_client.post(reverse("admin:shipping_countrygroup_add"), data)
    assert response.status_code == 302, response.content.decode()
    group = CountryGroup.objects.get(code="dach")
    assert group.countries == ["DE", "AT", "CH"]
    entry = AuditLog.objects.get(action="shipping.countrygroup.created")
    assert entry.target_key == str(group.public_id)


def test_a_country_group_in_use_is_protected_in_the_admin(staff_client: Client) -> None:
    group = CountryGroupFactory.create()
    ShippingZoneFactory.create(country_groups=[group.code])
    url = reverse("admin:shipping_countrygroup_delete", args=[group.pk])
    response = staff_client.post(url, {"post": "yes"})
    assert response.status_code == 200
    assert "protected" in response.content.decode()
    assert CountryGroup.objects.filter(pk=group.pk).exists()
    assert not domain_entries().exists()


def test_config_versions_are_read_only_in_the_admin(staff_client: Client) -> None:
    version = ShippingConfigVersionFactory.create()
    assert staff_client.get(reverse("admin:shipping_shippingconfigversion_add")).status_code == 403
    change_url = reverse("admin:shipping_shippingconfigversion_change", args=[version.pk])
    assert staff_client.get(change_url).status_code == 200  # view only
    assert staff_client.post(change_url, {"note": "edited"}).status_code == 403
    delete_url = reverse("admin:shipping_shippingconfigversion_delete", args=[version.pk])
    assert staff_client.post(delete_url, {"post": "yes"}).status_code == 403
