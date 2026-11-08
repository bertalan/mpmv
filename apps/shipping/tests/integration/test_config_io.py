"""SHP.13: export and import of the shipping configuration (JSON and CSV, format version 1) and
the platform's standard template that a vendor copies and customises.

Done when: a round trip export -> import is lossless; an invalid file is rejected with a per-row
report and changes nothing.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.shipping import config_format as fmt
from apps.shipping import engine
from apps.shipping.config_io import (
    ImportResult,
    InvalidConfig,
    copy_template,
    export_config,
    import_config,
    save_template,
    standard_template,
)
from apps.shipping.models import (
    CountryGroup,
    ShippingMethod,
    ShippingTemplate,
    ShippingZone,
    WeightBand,
)
from apps.shipping.services import save_config
from apps.shipping.tests.factories import (
    CarrierFactory,
    CountryGroupFactory,
    PickupOptionFactory,
    ShippingMethodFactory,
    ShippingTemplateFactory,
    ShippingZoneFactory,
    WeightBandFactory,
)
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:shipping", source="admin", ip="127.0.0.1")


def configured_vendor() -> Vendor:
    """A vendor with two own zones, a method on a platform zone, bands and a pickup option."""
    carrier = CarrierFactory.create(code="main")
    CountryGroupFactory.create(code="dach", countries=["AT", "CH", "DE"])
    world = ShippingZoneFactory.create(code="world", world=True)
    vendor = VendorFactory.create()
    home = ShippingZoneFactory.create(
        vendor=vendor, code="home", countries=["FR"], country_groups=["dach"]
    )
    islands = ShippingZoneFactory.create(
        vendor=vendor,
        code="islands",
        priority=10,
        countries=["GR"],
        postcode_patterns=["846*", "70000..74999"],
        states=["SS"],
    )
    standard = ShippingMethodFactory.create(
        vendor=vendor,
        zone=home,
        carrier=carrier,
        key="standard",
        default=True,
        free_shipping_threshold=Decimal("99.00"),
        tax_treatment=engine.STANDARD_RATE,
        over_max_weight=engine.QUOTE,
    )
    WeightBandFactory.create(method=standard, from_kg=Decimal(0), to_kg=Decimal(5))
    WeightBandFactory.create(
        method=standard,
        from_kg=Decimal(5),
        to_kg=None,
        cost_net=Decimal("19.90"),
        surcharge_net=Decimal("1.50"),
    )
    ShippingMethodFactory.create(
        vendor=vendor, zone=world, carrier=carrier, express=True, tax_treatment=engine.EXEMPT
    )
    ShippingMethodFactory.create(
        vendor=vendor, zone=islands, carrier=carrier, key="standard", default=True
    )
    PickupOptionFactory.create(vendor=vendor, key="shop", address="1 Example Street")
    return vendor


def index(rows: list[dict[str, Any]], **match: str) -> int:
    return next(i for i, row in enumerate(rows) if all(row[k] == v for k, v in match.items()))


def errors(result: ImportResult) -> set[tuple[str, str]]:
    return {(row.ref, field) for row in result.rows for field in row.errors}


def configuration_counts() -> tuple[int, ...]:
    return tuple(
        model.objects.count()
        for model in (CountryGroup, ShippingZone, ShippingMethod, WeightBand, ShippingTemplate)
    )


# --- Export


def test_the_export_describes_the_vendor_configuration() -> None:
    vendor = configured_vendor()
    document = export_config(vendor)

    assert (document["format"], document["version"]) == (fmt.FORMAT, fmt.VERSION)
    assert [zone["code"] for zone in document["zones"]] == ["islands", "home"]
    assert document["country_groups"] == [{"code": "dach", "countries": ["AT", "CH", "DE"]}]
    standard = document["methods"][index(document["methods"], zone="home", key="standard")]
    assert standard["zone_scope"] == fmt.OWN
    assert standard["tax_treatment"] == engine.STANDARD_RATE  # stored data only (D-03)
    assert standard["over_max_weight"] == engine.QUOTE  # stored data only (D-06)
    assert standard["free_shipping_threshold"] == "99.00"
    assert standard["bands"] == [
        {"from_kg": "0.000", "to_kg": "5.000", "cost_net": "9.90", "surcharge_net": "0.00"},
        {"from_kg": "5.000", "to_kg": None, "cost_net": "19.90", "surcharge_net": "1.50"},
    ]
    express = document["methods"][index(document["methods"], zone="world")]
    assert express["zone_scope"] == fmt.PLATFORM
    assert document["pickup_options"] == [
        {"key": "shop", "cost_net": "0.00", "address": "1 Example Street"}
    ]
    assert document["special_areas"] == []


def test_the_platform_export_holds_country_groups_and_platform_zones() -> None:
    configured_vendor()
    document = export_config(None)
    assert [zone["code"] for zone in document["zones"]] == ["world"]
    assert "dach" in [group["code"] for group in document["country_groups"]]
    assert document["methods"] == []
    assert document["pickup_options"] == []


# --- Round trip (Done when: lossless)


def test_a_json_round_trip_into_another_vendor_is_lossless() -> None:
    document = export_config(configured_vendor())
    other = VendorFactory.create()

    result = import_config(fmt.parse_json(fmt.dump_json(document)), vendor=other, ctx=CTX)

    assert result.ok, result.to_dict()
    assert result.applied
    assert export_config(other) == document


def test_a_csv_round_trip_into_another_vendor_is_lossless() -> None:
    document = export_config(configured_vendor())
    other = VendorFactory.create()

    result = import_config(fmt.parse_csv(fmt.dump_csv(document)), vendor=other, ctx=CTX)

    assert result.ok, result.to_dict()
    assert export_config(other) == document


def test_importing_a_vendor_export_back_changes_nothing() -> None:
    vendor = configured_vendor()
    document = export_config(vendor)

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok
    assert result.changeset.is_empty
    assert {row.status for row in result.rows} == {"unchanged"}
    assert not AuditLog.objects.exists()


# --- Dry run and audit


def test_a_dry_run_reports_the_changes_and_writes_nothing() -> None:
    document = export_config(configured_vendor())
    other = VendorFactory.create()
    before = configuration_counts()

    result = import_config(fmt.parse_json(document), vendor=other, ctx=CTX, dry_run=True)

    assert result.ok
    assert not result.applied
    # 2 zones, 3 methods, 2 bands, 1 pickup option; the country group already exists
    assert result.changeset.summary() == {"create": 8, "update": 0, "delete": 0}
    assert {row.status for row in result.rows} == {"created", "unchanged"}
    assert configuration_counts() == before
    assert not ShippingZone.objects.filter(vendor=other).exists()
    assert not AuditLog.objects.exists()


def test_an_import_audits_every_row_and_the_file() -> None:
    document = export_config(configured_vendor())
    other = VendorFactory.create()

    import_config(fmt.parse_json(document), vendor=other, ctx=CTX, source="upload.json")

    actions = list(AuditLog.objects.values_list("action", flat=True))
    assert actions.count("shipping.shippingzone.created") == 2
    assert actions.count("shipping.shippingmethod.created") == 3
    assert actions.count("shipping.weightband.created") == 2
    assert actions.count("shipping.pickupoption.created") == 1
    summary = AuditLog.objects.get(action="shipping.config.imported")
    assert summary.vendor_key == str(other.public_id)
    after = summary.after or {}
    assert after["source"] == "upload.json"
    assert after["version"] == fmt.VERSION
    assert after["summary"] == {"create": 8, "update": 0, "delete": 0}


# --- Invalid files (Done when: per-row report, nothing changes)


def test_an_invalid_file_is_rejected_with_a_per_row_report_and_changes_nothing() -> None:
    vendor = configured_vendor()
    document = export_config(vendor)
    zones, methods = document["zones"], document["methods"]
    zones[index(zones, code="home")]["name"] = "Home, renamed"  # a valid change
    zones[index(zones, code="islands")]["postcode_patterns"] = ["70000-74999"]
    standard = index(methods, zone="home", key="standard")
    methods[standard]["bands"][1]["from_kg"] = "4.000"  # overlaps 0-5
    methods[standard]["tax_treatment"] = "zero"
    express = index(methods, zone="world")
    methods[express]["carrier"] = "nobody"
    methods[express]["zone"] = "nowhere"
    exported = export_config(vendor)

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert not result.ok
    assert not result.applied
    assert errors(result) == {
        (f"zones[{index(zones, code='islands')}]", "postcode_patterns"),
        (f"methods[{standard}]", "tax_treatment"),
        (f"methods[{standard}].bands[1]", "from_kg"),
        (f"methods[{express}]", "carrier"),
        (f"methods[{express}]", "zone"),
    }
    report = result.to_dict()
    assert report["ok"] is False
    hints = [
        message for row in report["rows"] for message in row["errors"].get("postcode_patterns", [])
    ]
    assert any("70000..74999" in message for message in hints)  # the shared validator's hint
    assert export_config(vendor) == exported
    assert not AuditLog.objects.exists()


def test_a_database_rule_broken_late_rolls_back_the_rows_before_it() -> None:
    document = export_config(configured_vendor())
    other = VendorFactory.create()
    home = ShippingZoneFactory.create(vendor=other, code="home", countries=["FR"])
    ShippingMethodFactory.create(vendor=other, zone=home, key="legacy", default=True)
    before = configuration_counts()

    result = import_config(fmt.parse_json(document), vendor=other, ctx=CTX)

    assert not result.ok
    standard = index(document["methods"], zone="home", key="standard")
    assert (f"methods[{standard}]", "__all__") in errors(result)
    assert configuration_counts() == before  # the zones and methods before it are rolled back
    assert not ShippingZone.objects.filter(vendor=other, code="islands").exists()
    assert not AuditLog.objects.exists()


def test_a_structurally_broken_file_is_refused_before_any_check() -> None:
    vendor = configured_vendor()
    result = import_config(fmt.parse_json('{"format": "other"}'), vendor=vendor, ctx=CTX)
    assert not result.ok
    assert ("file", "format") in errors(result)


# --- Weight bands and country groups


def test_the_bands_of_an_imported_method_replace_the_stored_ones() -> None:
    vendor = configured_vendor()
    document = export_config(vendor)
    standard = index(document["methods"], zone="home", key="standard")
    document["methods"][standard]["bands"] = [
        {"from_kg": "0", "to_kg": "2", "cost_net": "5.00", "surcharge_net": "0"},
        {"from_kg": "2", "to_kg": None, "cost_net": "12.00", "surcharge_net": "0"},
    ]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert result.changeset.summary() == {"create": 1, "update": 1, "delete": 1}
    bands = WeightBand.objects.filter(method__vendor=vendor, method__zone__code="home")
    assert [(b.from_kg, b.to_kg, b.cost_net) for b in bands.order_by("from_kg")] == [
        (Decimal("0.000"), Decimal("2.000"), Decimal("5.00")),
        (Decimal("2.000"), None, Decimal("12.00")),
    ]
    assert AuditLog.objects.filter(action="shipping.weightband.deleted").count() == 1


def stored_bands(vendor: Vendor) -> list[tuple[Decimal, Decimal | None, Decimal]]:
    bands = WeightBand.objects.filter(method__vendor=vendor, method__zone__code="home")
    return [(b.from_kg, b.to_kg, b.cost_net) for b in bands.order_by("from_kg")]


STORED_BANDS = [
    (Decimal("0.000"), Decimal("5.000"), Decimal("9.90")),
    (Decimal("5.000"), None, Decimal("19.90")),
]


def test_a_method_without_a_band_list_keeps_its_stored_bands() -> None:
    vendor = configured_vendor()
    document = fmt.empty_document()
    document["methods"] = [{"zone": "home", "key": "standard", "carrier": "main"}]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert result.changeset.is_empty
    assert stored_bands(vendor) == STORED_BANDS


def test_an_empty_band_list_removes_the_stored_bands() -> None:
    vendor = configured_vendor()
    document = fmt.empty_document()
    document["methods"] = [{"zone": "home", "key": "standard", "carrier": "main", "bands": []}]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert result.changeset.summary() == {"create": 0, "update": 0, "delete": 2}
    assert stored_bands(vendor) == []


def test_a_csv_method_without_band_lines_keeps_its_stored_bands() -> None:
    vendor = configured_vendor()
    document = export_config(vendor)
    for method in document["methods"]:
        method["bands"] = []
    text = fmt.dump_csv(document)

    result = import_config(fmt.parse_csv(text), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert result.changeset.is_empty
    assert stored_bands(vendor) == STORED_BANDS


def test_a_partial_row_updates_only_the_fields_it_names() -> None:
    vendor = configured_vendor()
    document = fmt.empty_document()
    document["zones"] = [{"code": "home", "name": "Home renamed"}]
    document["methods"] = [
        {
            "zone": "home",
            "key": "standard",  # no carrier: the stored one stays
            "max_delivery_days": 9,
            "bands": [
                {"from_kg": "0", "cost_net": "8.00"},  # to_kg and surcharge_net stay
                {"from_kg": "5", "to_kg": None},  # cost_net stays
            ],
        }
    ]
    document["pickup_options"] = [{"key": "shop", "cost_net": "2.00"}]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert result.changeset.summary() == {"create": 0, "update": 4, "delete": 0}
    zone = ShippingZone.objects.get(vendor=vendor, code="home")
    assert (zone.name, zone.priority, zone.countries) == ("Home renamed", 100, ["FR"])
    method = ShippingMethod.objects.get(vendor=vendor, zone=zone, key="standard")
    assert (method.carrier.code, method.is_default, method.max_delivery_days) == ("main", True, 9)
    assert method.free_shipping_threshold == Decimal("99.00")
    assert stored_bands(vendor) == [
        (Decimal("0.000"), Decimal("5.000"), Decimal("8.00")),
        (Decimal("5.000"), None, Decimal("19.90")),
    ]
    pickup = vendor.pickup_options.get(key="shop")
    assert (pickup.cost_net, pickup.address) == (Decimal("2.00"), "1 Example Street")


def test_a_partial_row_is_checked_with_its_stored_values() -> None:
    vendor = configured_vendor()
    document = fmt.empty_document()
    document["methods"] = [
        {"zone": "home", "key": "standard", "min_delivery_days": 30}  # the stored max is 5
    ]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert ("methods[0]", "__all__") in errors(result)
    assert not result.applied


def test_bands_in_any_order_can_split_a_stored_band() -> None:
    vendor = configured_vendor()
    document = export_config(vendor)
    standard = index(document["methods"], zone="home", key="standard")
    document["methods"][standard]["bands"] = [
        {"from_kg": "5", "to_kg": None, "cost_net": "19.90", "surcharge_net": "1.50"},
        {"from_kg": "2", "to_kg": "5", "cost_net": "7.00", "surcharge_net": "0"},
        {"from_kg": "0", "to_kg": "2", "cost_net": "5.00", "surcharge_net": "0"},
    ]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert stored_bands(vendor) == [
        (Decimal("0.000"), Decimal("2.000"), Decimal("5.00")),
        (Decimal("2.000"), Decimal("5.000"), Decimal("7.00")),
        (Decimal("5.000"), None, Decimal("19.90")),
    ]


def test_a_vendor_file_never_changes_the_platform_country_groups() -> None:
    vendor = configured_vendor()
    document = export_config(vendor)
    document["country_groups"][0]["countries"] = ["AT", "DE"]
    document["country_groups"].append({"code": "nordics", "countries": ["DK", "SE"]})

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert errors(result) == {("country_groups[1]", "code")}
    changed = next(row for row in result.rows if row.ref == "country_groups[0]")
    assert changed.status == "skipped"
    assert changed.warnings
    assert CountryGroup.objects.get(code="dach").countries == ["AT", "CH", "DE"]
    assert not CountryGroup.objects.filter(code="nordics").exists()


def test_a_platform_file_writes_country_groups_and_platform_zones() -> None:
    document = fmt.empty_document()
    document["country_groups"] = [{"code": "nordics", "countries": ["dk", "SE"]}]
    document["zones"] = [
        {"code": "north", "name": "North", "priority": 50, "country_groups": ["nordics"]}
    ]

    result = import_config(fmt.parse_json(document), vendor=None, ctx=CTX)

    assert result.ok, result.to_dict()
    assert CountryGroup.objects.get(code="nordics").countries == ["DK", "SE"]
    zone = ShippingZone.objects.get(code="north")
    assert zone.vendor is None
    assert zone.country_groups == ["nordics"]
    assert export_config(None)["zones"][0]["code"] == "north"


def test_a_platform_file_holds_no_vendor_rows() -> None:
    document = export_config(configured_vendor())
    result = import_config(fmt.parse_json(document), vendor=None, ctx=CTX)
    assert not result.ok
    assert ("methods[0]", "vendor") in errors(result)
    assert ("pickup_options[0]", "vendor") in errors(result)


# --- The standard template


def test_the_standard_template_is_validated_before_it_is_saved() -> None:
    CarrierFactory.create(code="main")
    document = fmt.empty_document()
    document["zones"] = [{"code": "home", "name": "Home", "priority": 0, "countries": ["XX"]}]
    document["methods"] = [{"zone": "elsewhere", "carrier": "main"}]
    template = ShippingTemplateFactory.build(document=document, is_standard=True)

    with pytest.raises(InvalidConfig) as raised:
        save_template(template, ctx=CTX)

    refs = {(row.ref, field) for row in raised.value.rows for field in row.errors}
    assert refs == {
        ("zones[0]", "priority"),
        ("zones[0]", "countries"),
        ("methods[0]", "zone"),
    }
    assert not ShippingTemplate.objects.exists()
    assert not AuditLog.objects.exists()


def test_a_vendor_copies_the_standard_template_and_customises_it() -> None:
    document = export_config(configured_vendor())
    del document["pickup_options"][0]  # a template proposes shipping, not a pickup address
    template = ShippingTemplateFactory.build(
        code="standard", name="Standard", is_standard=True, document=document
    )
    save_template(template, ctx=CTX)
    assert AuditLog.objects.filter(action="shipping.shippingtemplate.created").exists()
    assert standard_template() == template

    vendor = VendorFactory.create()
    preview = copy_template(template, vendor, ctx=CTX, dry_run=True)
    assert preview.ok
    assert not preview.applied
    assert not ShippingZone.objects.filter(vendor=vendor).exists()

    copy_template(template, vendor, ctx=CTX)
    assert export_config(vendor) == document
    copied = AuditLog.objects.get(
        action="shipping.config.imported", vendor_key=str(vendor.public_id)
    )
    assert (copied.after or {})["source"] == "template:standard"

    band = WeightBand.objects.get(method__vendor=vendor, method__zone__code="home", to_kg=5)
    band.cost_net = Decimal("7.50")
    save_config(band, ctx=CTX)  # the vendor customises its own copy

    template.refresh_from_db()
    assert template.document == document
    later = VendorFactory.create()
    copy_template(template, later, ctx=CTX)
    assert WeightBand.objects.get(
        method__vendor=later, method__zone__code="home", to_kg=5
    ).cost_net == Decimal("9.90")


def test_there_is_at_most_one_standard_template() -> None:
    ShippingTemplateFactory.create(is_standard=True)
    ShippingTemplateFactory.create(is_standard=False)
    with pytest.raises(IntegrityError):
        ShippingTemplateFactory.create(is_standard=True)


def test_without_a_standard_template_there_is_nothing_to_copy() -> None:
    ShippingTemplateFactory.create(is_standard=False)
    assert standard_template() is None
