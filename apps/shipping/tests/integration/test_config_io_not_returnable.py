"""VEN.36 follow-up: `not_returnable_by_post` in the shipping configuration file format
(version 3), JSON and CSV."""

import pytest

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.shipping import config_format as fmt
from apps.shipping.config_io import ImportResult, export_config, import_config
from apps.shipping.models import ShippingMethod
from apps.shipping.tests.factories import (
    CarrierFactory,
    ShippingMethodFactory,
    ShippingZoneFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:shipping", source="admin", ip="127.0.0.1")


def vendor_with_pallet() -> Vendor:
    carrier = CarrierFactory.create(code="main")
    vendor = VendorFactory.create()
    zone = ShippingZoneFactory.create(vendor=vendor, code="home", countries=["FR"])
    ShippingMethodFactory.create(
        vendor=vendor, zone=zone, carrier=carrier, key="pallet", not_returnable_by_post=True
    )
    ShippingMethodFactory.create(vendor=vendor, zone=zone, carrier=carrier, key="parcel")
    return vendor


def flags(vendor: Vendor) -> dict[str, bool]:
    return {m.key: m.not_returnable_by_post for m in ShippingMethod.objects.filter(vendor=vendor)}


def errors(result: ImportResult) -> set[tuple[str, str]]:
    return {(row.ref, field) for row in result.rows for field in row.errors}


def test_the_export_includes_the_flag_in_version_3() -> None:
    document = export_config(vendor_with_pallet())

    assert document["version"] == fmt.VERSION == 3
    by_key = {m["key"]: m for m in document["methods"]}
    assert by_key["pallet"]["not_returnable_by_post"] is True
    assert by_key["parcel"]["not_returnable_by_post"] is False


def test_the_flag_round_trips_through_json() -> None:
    document = export_config(vendor_with_pallet())
    other = VendorFactory.create()

    result = import_config(fmt.parse_json(fmt.dump_json(document)), vendor=other, ctx=CTX)

    assert result.ok, result.to_dict()
    assert flags(other) == {"pallet": True, "parcel": False}


def test_the_flag_round_trips_through_csv() -> None:
    document = export_config(vendor_with_pallet())
    other = VendorFactory.create()

    result = import_config(fmt.parse_csv(fmt.dump_csv(document)), vendor=other, ctx=CTX)

    assert result.ok, result.to_dict()
    assert flags(other) == {"pallet": True, "parcel": False}
    assert export_config(other) == document


def test_a_version_2_file_without_the_flag_still_imports() -> None:
    kept = vendor_with_pallet()
    document = export_config(kept)
    document["version"] = 2
    for method in document["methods"]:
        del method["not_returnable_by_post"]
    other = VendorFactory.create()

    created = import_config(fmt.parse_json(document), vendor=other, ctx=CTX)

    assert created.ok, created.to_dict()
    assert flags(other) == {"pallet": False, "parcel": False}  # the model default on create

    updated = import_config(fmt.parse_json(document), vendor=kept, ctx=CTX)

    assert updated.ok, updated.to_dict()
    assert flags(kept) == {"pallet": True, "parcel": False}  # the stored value is kept


def test_an_invalid_flag_gives_a_per_row_error_and_changes_nothing() -> None:
    vendor = vendor_with_pallet()
    document = export_config(vendor)
    document["zones"][0]["name"] = "Renamed"  # a valid change that must not be written
    pallet = next(i for i, m in enumerate(document["methods"]) if m["key"] == "pallet")
    document["methods"][pallet]["not_returnable_by_post"] = "maybe"
    exported = export_config(vendor)
    AuditLog.objects.all().delete()

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert not result.ok
    assert not result.applied
    assert errors(result) == {(f"methods[{pallet}]", "not_returnable_by_post")}
    assert export_config(vendor) == exported
    assert not AuditLog.objects.exists()


def test_an_invalid_csv_flag_gives_a_per_row_error() -> None:
    document = export_config(vendor_with_pallet())
    lines = fmt.dump_csv(document).splitlines()
    method_line = next(i for i, line in enumerate(lines) if line.startswith("method,"))
    head, _, _ = lines[method_line].rpartition(",")
    lines[method_line] = f"{head},maybe"

    result = import_config(
        fmt.parse_csv("\n".join(lines) + "\n"), vendor=VendorFactory.create(), ctx=CTX
    )

    assert not result.ok
    assert {field for _, field in errors(result)} == {"not_returnable_by_post"}


def test_a_changed_flag_is_audited_as_an_update() -> None:
    vendor = vendor_with_pallet()
    document = export_config(vendor)
    for method in document["methods"]:
        method["not_returnable_by_post"] = not method["not_returnable_by_post"]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok
    assert result.applied
    assert result.changeset.summary() == {"create": 0, "update": 2, "delete": 0}
    assert flags(vendor) == {"pallet": False, "parcel": True}
    assert AuditLog.objects.filter(action="shipping.shippingmethod.updated").count() == 2
