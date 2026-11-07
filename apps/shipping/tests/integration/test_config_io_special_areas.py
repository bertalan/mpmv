"""SHP.14: special areas and method treatments in the export, the import and the standard
template (configuration file format version 2).

Special areas are platform data, like country groups: a platform file writes them (a carrier's
remote-area list is imported this way); a vendor file or a template names them and never changes
them. A method's `area_treatments` replace its stored treatments; a method without the key keeps
them. Version 1 files stay importable.
"""

from decimal import Decimal
from typing import Any

import pytest

from apps.core.models import AuditLog
from apps.shipping import config_format as fmt
from apps.shipping import engine
from apps.shipping.config_io import (
    InvalidConfig,
    copy_template,
    export_config,
    import_config,
    save_template,
)
from apps.shipping.models import SpecialArea, SpecialAreaTreatment
from apps.shipping.tests.factories import ShippingTemplateFactory, SpecialAreaTreatmentFactory
from apps.shipping.tests.integration.test_config_io import CTX, configured_vendor, errors, index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

FIXED_15 = {
    "area": "it-sardinia",
    "mode": engine.SURCHARGE,
    "surcharge_type": engine.FIXED,
    "surcharge": "15.00",
    "extra_days": 2,
}


def vendor_with_treatments() -> Vendor:
    vendor = configured_vendor()
    method = vendor.shipping_methods.get(zone__code="home", key="standard")
    SpecialAreaTreatmentFactory.create(
        method=method,
        area=SpecialArea.objects.get(code="it-sardinia"),
        mode=engine.SURCHARGE,
        surcharge_type=engine.FIXED,
        surcharge=Decimal("15.00"),
        extra_days=2,
    )
    SpecialAreaTreatmentFactory.create(
        method=method, area=SpecialArea.objects.get(code="it-livigno"), mode=engine.BLOCKED
    )
    return vendor


def stored_treatments(vendor: Vendor) -> list[tuple[str, str, str, Decimal, int]]:
    rows = SpecialAreaTreatment.objects.filter(method__vendor=vendor).order_by("area__code")
    return [(t.area.code, t.mode, t.surcharge_type, t.surcharge, t.extra_days) for t in rows]


# --- Export


def test_a_vendor_export_lists_its_treatments_and_the_areas_they_name() -> None:
    document = export_config(vendor_with_treatments())

    assert document["version"] == fmt.VERSION == 2
    standard = document["methods"][index(document["methods"], zone="home", key="standard")]
    assert standard["area_treatments"] == [
        {
            "area": "it-livigno",
            "mode": engine.BLOCKED,
            "surcharge_type": "",
            "surcharge": "0.00",
            "extra_days": 0,
        },
        {**FIXED_15},
    ]
    express = document["methods"][index(document["methods"], zone="world")]
    assert express["area_treatments"] == []
    assert [area["code"] for area in document["special_areas"]] == ["it-livigno", "it-sardinia"]
    livigno = document["special_areas"][0]
    assert livigno["territory_code"] == "IT-LIVIGNO"
    assert livigno["kind"] == engine.EXTRA_VAT


def test_the_platform_export_holds_every_special_area() -> None:
    document = export_config(None)
    codes = [area["code"] for area in document["special_areas"]]
    assert codes == sorted(SpecialArea.objects.values_list("code", flat=True))
    assert "it-sardinia" in codes


# --- Round trips


def test_a_json_round_trip_with_treatments_is_lossless() -> None:
    document = export_config(vendor_with_treatments())
    other = VendorFactory.create()

    result = import_config(fmt.parse_json(fmt.dump_json(document)), vendor=other, ctx=CTX)

    assert result.ok, result.to_dict()
    assert export_config(other) == document
    assert AuditLog.objects.filter(action="shipping.specialareatreatment.created").count() == 2


def test_a_csv_round_trip_with_treatments_is_lossless() -> None:
    document = export_config(vendor_with_treatments())
    other = VendorFactory.create()

    result = import_config(fmt.parse_csv(fmt.dump_csv(document)), vendor=other, ctx=CTX)

    assert result.ok, result.to_dict()
    assert export_config(other) == document


def test_importing_an_export_with_treatments_back_changes_nothing() -> None:
    vendor = vendor_with_treatments()
    result = import_config(fmt.parse_json(export_config(vendor)), vendor=vendor, ctx=CTX)
    assert result.ok, result.to_dict()
    assert result.changeset.is_empty


def test_a_version_1_file_is_still_imported() -> None:
    document = export_config(configured_vendor())
    document["version"] = 1
    document["special_areas"] = []
    for method in document["methods"]:
        del method["area_treatments"]
    other = VendorFactory.create()

    result = import_config(fmt.parse_json(document), vendor=other, ctx=CTX)

    assert result.ok, result.to_dict()
    assert result.applied
    summary = AuditLog.objects.get(action="shipping.config.imported")
    assert (summary.after or {})["version"] == 1


# --- Treatments replace the stored ones


def test_the_treatments_of_an_imported_method_replace_the_stored_ones() -> None:
    vendor = vendor_with_treatments()
    document = fmt.empty_document()
    document["methods"] = [
        {
            "zone": "home",
            "key": "standard",
            "area_treatments": [
                {**FIXED_15, "surcharge": "18.00"},
                {"area": "gr-crete", "mode": engine.QUOTE_ONLY},
            ],
        }
    ]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert result.changeset.summary() == {"create": 1, "update": 1, "delete": 1}
    assert stored_treatments(vendor) == [
        ("gr-crete", engine.QUOTE_ONLY, "", Decimal("0.00"), 0),
        ("it-sardinia", engine.SURCHARGE, engine.FIXED, Decimal("18.00"), 2),
    ]
    assert AuditLog.objects.filter(action="shipping.specialareatreatment.deleted").count() == 1


def test_a_method_without_a_treatment_list_keeps_its_treatments() -> None:
    vendor = vendor_with_treatments()
    before = stored_treatments(vendor)
    document = fmt.empty_document()
    document["methods"] = [{"zone": "home", "key": "standard", "max_delivery_days": 6}]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert stored_treatments(vendor) == before


def test_an_empty_treatment_list_removes_them() -> None:
    vendor = vendor_with_treatments()
    document = fmt.empty_document()
    document["methods"] = [{"zone": "home", "key": "standard", "area_treatments": []}]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert stored_treatments(vendor) == []


def test_invalid_treatments_are_reported_per_row_and_change_nothing() -> None:
    vendor = vendor_with_treatments()
    before = stored_treatments(vendor)
    document = fmt.empty_document()
    document["methods"] = [
        {
            "zone": "home",
            "key": "standard",
            "area_treatments": [
                {"area": "atlantis", "mode": engine.INDICATIVE},  # no such area
                {"area": "it-sardinia", "mode": engine.SURCHARGE},  # a surcharge of what?
                {"area": "gr-crete", "mode": engine.BLOCKED, "extra_days": 3},
            ],
        }
    ]

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert not result.applied
    assert errors(result) == {
        ("methods[0].area_treatments[0]", "area"),
        ("methods[0].area_treatments[1]", "surcharge_type"),
        ("methods[0].area_treatments[2]", "extra_days"),
    }
    assert stored_treatments(vendor) == before


# --- Special areas are platform data


def carrier_remote_list() -> dict[str, Any]:
    document = fmt.empty_document()
    document["special_areas"] = [
        {
            "code": "carrier-remote-gr",
            "name": "Carrier remote areas (GR)",
            "kind": engine.REMOTE,
            "countries": ["el"],
            "postcode_patterns": ["190 10", "18010..18020"],
        }
    ]
    return document


def test_a_platform_file_imports_a_carrier_remote_area_list() -> None:
    result = import_config(fmt.parse_json(carrier_remote_list()), vendor=None, ctx=CTX)

    assert result.ok, result.to_dict()
    area = SpecialArea.objects.get(code="carrier-remote-gr")
    assert (area.kind, area.countries, area.postcode_patterns) == (
        engine.REMOTE,
        ["GR"],
        ["190 10", "18010..18020"],
    )
    assert AuditLog.objects.filter(action="shipping.specialarea.created").count() == 1


def test_a_platform_csv_imports_a_carrier_remote_area_list() -> None:
    text = fmt.dump_csv(carrier_remote_list())
    result = import_config(fmt.parse_csv(text), vendor=None, ctx=CTX)
    assert result.ok, result.to_dict()
    assert SpecialArea.objects.filter(code="carrier-remote-gr").exists()


def test_a_platform_file_validates_special_areas_with_the_model() -> None:
    document = carrier_remote_list()
    document["special_areas"][0]["postcode_patterns"] = ["18010-18020"]
    document["special_areas"].append(
        {"code": "nowhere", "name": "No list", "kind": engine.EXTRA_VAT, "countries": ["GR"]}
    )

    result = import_config(fmt.parse_json(document), vendor=None, ctx=CTX)

    assert not result.applied
    assert ("special_areas[0]", "postcode_patterns") in errors(result)
    assert ("special_areas[1]", "__all__") in errors(result)
    assert not SpecialArea.objects.filter(code__in=["carrier-remote-gr", "nowhere"]).exists()


def test_a_vendor_file_never_changes_the_platform_special_areas() -> None:
    vendor = vendor_with_treatments()
    document = export_config(vendor)
    document["special_areas"][1]["postcode_patterns"] = ["07*"]
    document["special_areas"].append(carrier_remote_list()["special_areas"][0])

    result = import_config(fmt.parse_json(document), vendor=vendor, ctx=CTX)

    assert errors(result) == {("special_areas[2]", "code")}
    changed = next(row for row in result.rows if row.ref == "special_areas[1]")
    assert changed.status == "skipped"
    assert changed.warnings
    assert SpecialArea.objects.get(code="it-sardinia").postcode_patterns != ["07*"]
    assert not SpecialArea.objects.filter(code="carrier-remote-gr").exists()


def test_a_platform_file_holds_no_treatments() -> None:
    document = fmt.empty_document()
    document["methods"] = [
        {"zone": "world", "zone_scope": fmt.PLATFORM, "key": "x", "area_treatments": [FIXED_15]}
    ]

    result = import_config(fmt.parse_json(document), vendor=None, ctx=CTX)

    assert ("methods[0]", "vendor") in errors(result)
    assert ("methods[0].area_treatments[0]", "vendor") in errors(result)


# --- The standard template carries the special areas and their treatments


def test_a_vendor_copies_the_special_area_treatments_of_the_template() -> None:
    document = export_config(vendor_with_treatments())
    del document["pickup_options"][0]
    template = ShippingTemplateFactory.build(code="standard", is_standard=True, document=document)
    save_template(template, ctx=CTX)

    vendor = VendorFactory.create()
    result = copy_template(template, vendor, ctx=CTX)

    assert result.ok, result.to_dict()
    assert export_config(vendor) == document
    assert stored_treatments(vendor)[1] == (
        "it-sardinia",
        engine.SURCHARGE,
        engine.FIXED,
        Decimal("15.00"),
        2,
    )


def test_a_template_naming_an_unknown_area_is_refused() -> None:
    document = export_config(vendor_with_treatments())
    del document["pickup_options"][0]
    standard = index(document["methods"], zone="home", key="standard")
    document["methods"][standard]["area_treatments"][0]["area"] = "atlantis"
    template = ShippingTemplateFactory.build(document=document)

    with pytest.raises(InvalidConfig) as raised:
        save_template(template, ctx=CTX)

    refs = {(row.ref, name) for row in raised.value.rows for name in row.errors}
    assert refs == {(f"methods[{standard}].area_treatments[0]", "area")}
