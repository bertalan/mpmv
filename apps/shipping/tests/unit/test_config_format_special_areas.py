"""SHP.14: special areas in the shipping configuration file format (version 2).

Version 2 fills the `special_areas` section that version 1 reserved, and lets a method list the
treatment it gives each area (`methods[].area_treatments`). Version 1 files stay readable.
"""

import copy
import json
from typing import Any

from apps.shipping import config_format as fmt
from apps.shipping.tests.unit.test_config_format import messages, sample_document


def document_v2() -> dict[str, Any]:
    document = sample_document()
    document["special_areas"] = [
        {
            "code": "it-sardinia",
            "name": "Sardinia",
            "kind": "island",
            "countries": ["IT"],
            "postcode_patterns": ["07*", "08*", "09*"],
            "states": ["SS", "NU"],
            "territory_code": "",
        },
        {
            "code": "it-livigno",
            "name": "Livigno",
            "kind": "extra_vat",
            "countries": ["IT"],
            "postcode_patterns": ["23041"],
            "states": [],
            "territory_code": "IT-LIVIGNO",
        },
    ]
    document["methods"][0]["area_treatments"] = [
        {
            "area": "it-sardinia",
            "mode": "surcharge",
            "surcharge_type": "fixed",
            "surcharge": "15.00",
            "extra_days": 2,
        },
        {
            "area": "it-livigno",
            "mode": "blocked",
            "surcharge_type": "",
            "surcharge": "0.00",
            "extra_days": 0,
        },
    ]
    return document


def test_the_current_version_is_2() -> None:
    assert fmt.VERSION == 2
    assert fmt.empty_document()["version"] == 2


def test_a_version_2_document_round_trips_through_its_rows() -> None:
    document = document_v2()
    parsed = fmt.parse_json(json.dumps(document))
    assert parsed.ok, parsed.issues
    assert fmt.to_document(parsed) == document


def test_special_areas_and_treatments_are_rows_of_the_file() -> None:
    parsed = fmt.parse_json(document_v2())
    refs = {(row.record, row.ref) for row in parsed.rows}
    assert ("special_area", "special_areas[1]") in refs
    assert ("area_treatment", "methods[0].area_treatments[1]") in refs
    treatment = next(row for row in parsed.rows if row.ref == "methods[0].area_treatments[0]")
    assert treatment.method == (fmt.OWN, "home", "standard")
    assert treatment.label == "own:home/standard it-sardinia"
    # Only the methods that list their treatments replace the stored ones.
    assert parsed.treatment_methods == {(fmt.OWN, "home", "standard")}


def test_a_version_2_csv_round_trips_through_its_rows() -> None:
    text = fmt.dump_csv(document_v2())
    parsed = fmt.parse_csv(text)
    assert parsed.ok, parsed.issues
    assert fmt.dump_csv(fmt.to_document(parsed)) == text
    assert [row.record for row in parsed.rows] == [
        "country_group",
        "zone",
        "zone",
        "method",
        "band",
        "band",
        "area_treatment",
        "area_treatment",
        "method",
        "pickup_option",
        "special_area",
        "special_area",
    ]
    assert parsed.treatment_methods == {(fmt.OWN, "home", "standard")}
    sardinia = next(row for row in parsed.rows if row.values.get("code") == "it-sardinia")
    assert sardinia.values["postcode_patterns"] == ["07*", "08*", "09*"]
    surcharge = next(row for row in parsed.rows if row.values.get("area") == "it-sardinia")
    assert surcharge.values["surcharge"] == "15.00"


def test_a_version_1_document_is_still_read() -> None:
    document = sample_document()
    document["version"] = 1
    parsed = fmt.parse_json(document)

    assert parsed.ok, parsed.issues
    assert parsed.version == 1
    # It is written back in the current version, which only adds to version 1.
    assert fmt.to_document(parsed) == {**document, "version": fmt.VERSION}


def test_a_version_1_csv_file_is_still_read() -> None:
    lines = fmt.dump_csv(sample_document()).splitlines()
    lines[1] = lines[1].replace(f"format,{fmt.FORMAT},{fmt.VERSION},", f"format,{fmt.FORMAT},1,")
    parsed = fmt.parse_csv("\n".join(lines))
    assert parsed.ok, parsed.issues
    assert parsed.version == 1


def test_a_version_1_file_cannot_hold_area_treatments() -> None:
    document = document_v2()
    document["version"] = 1
    document["special_areas"] = []
    assert messages(fmt.parse_json(document)) == [("methods[0]", "area_treatments")]


def test_a_version_1_csv_cannot_hold_special_areas() -> None:
    lines = fmt.dump_csv(document_v2()).splitlines()
    lines[1] = lines[1].replace(f"format,{fmt.FORMAT},{fmt.VERSION},", f"format,{fmt.FORMAT},1,")
    found = messages(fmt.parse_csv("\n".join(lines)))
    assert found
    assert {name for _, name in found} == {"special_areas"}


def test_a_method_names_each_area_once() -> None:
    document = document_v2()
    treatments = document["methods"][0]["area_treatments"]
    treatments.append(copy.deepcopy(treatments[0]))
    del treatments[1]["area"]
    assert messages(fmt.parse_json(document)) == [
        ("methods[0].area_treatments[1]", "area"),
        ("methods[0].area_treatments[2]", "area"),
    ]


def test_two_methods_may_treat_the_same_area() -> None:
    document = document_v2()
    document["methods"][1]["area_treatments"] = copy.deepcopy(
        document["methods"][0]["area_treatments"]
    )
    assert fmt.parse_json(document).ok


def test_special_areas_are_named_once_per_file() -> None:
    document = document_v2()
    document["special_areas"].append(copy.deepcopy(document["special_areas"][0]))
    assert messages(fmt.parse_json(document)) == [("special_areas[2]", "code")]


def test_an_unknown_field_of_a_special_area_is_reported() -> None:
    document = document_v2()
    document["special_areas"][0]["surcharge"] = "5.00"
    assert messages(fmt.parse_json(document)) == [("special_areas[0]", "surcharge")]


def test_a_csv_treatment_needs_its_method_in_the_file() -> None:
    lines = fmt.dump_csv(document_v2()).splitlines()
    method_line = next(i for i, line in enumerate(lines) if line.startswith("method,"))
    del lines[method_line]
    found = messages(fmt.parse_csv("\n".join(lines)))
    treatment_lines = [ref for ref, name in found if name == "key"]
    assert len(treatment_lines) == 4  # two bands and two treatments lost their method


def test_an_empty_version_2_document_is_valid() -> None:
    parsed = fmt.parse_json(fmt.empty_document())
    assert parsed.ok
    assert parsed.rows == []
    assert parsed.treatment_methods == set()
