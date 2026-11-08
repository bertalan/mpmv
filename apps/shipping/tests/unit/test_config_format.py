"""SHP.13: the shipping configuration file format, version 1 (spec 06 §6.2 "Configuration file
format"). Pure parsing and writing of JSON and CSV; the values themselves are validated by the
models when the file is checked or imported (integration tests)."""

import copy
import json
from typing import Any

from apps.shipping import config_format as fmt


def sample_document() -> dict[str, Any]:
    return {
        "format": fmt.FORMAT,
        "version": fmt.VERSION,
        "country_groups": [{"code": "dach", "countries": ["AT", "CH", "DE"]}],
        "zones": [
            {
                "code": "home",
                "name": "Home",
                "priority": 100,
                "countries": ["FR"],
                "country_groups": ["dach"],
                "postcode_patterns": [],
                "states": [],
            },
            {
                "code": "islands",
                "name": "Islands",
                "priority": 10,
                "countries": ["GR"],
                "country_groups": [],
                "postcode_patterns": ["846*", "70000..74999", "BT1 1AA"],
                "states": ["SS"],
            },
        ],
        "methods": [
            {
                "zone": "home",
                "zone_scope": fmt.OWN,
                "carrier": "main",
                "key": "standard",
                "service_level": "standard",
                "min_delivery_days": 2,
                "max_delivery_days": 5,
                "is_default": True,
                "free_shipping_threshold": "99.00",
                "tax_treatment": "follows_goods",
                "over_max_weight": "",
                "bands": [
                    {
                        "from_kg": "0.000",
                        "to_kg": "5.000",
                        "cost_net": "9.90",
                        "surcharge_net": "0",
                    },
                    {
                        "from_kg": "5.000",
                        "to_kg": None,
                        "cost_net": "19.90",
                        "surcharge_net": "1.50",
                    },
                ],
            },
            {
                "zone": "world",
                "zone_scope": fmt.PLATFORM,
                "carrier": "main",
                "key": "express",
                "service_level": "express",
                "min_delivery_days": None,
                "max_delivery_days": None,
                "is_default": False,
                "free_shipping_threshold": None,
                "tax_treatment": "exempt",
                "over_max_weight": "quote",
                "bands": [],
            },
        ],
        "pickup_options": [{"key": "shop", "cost_net": "0.00", "address": "1 Example Street"}],
        "special_areas": [],
    }


def messages(parsed: fmt.ParsedConfig) -> list[tuple[str, str]]:
    return [(issue.ref, issue.field) for issue in parsed.issues]


def test_a_json_document_round_trips_through_its_rows() -> None:
    document = sample_document()
    parsed = fmt.parse_json(json.dumps(document))
    assert parsed.ok, parsed.issues
    assert fmt.to_document(parsed) == document


def test_the_rows_keep_their_place_in_the_file_for_the_report() -> None:
    parsed = fmt.parse_json(sample_document())
    refs = [(row.record, row.ref) for row in parsed.rows]
    assert ("zone", "zones[1]") in refs
    assert ("band", "methods[0].bands[1]") in refs
    band = next(row for row in parsed.rows if row.ref == "methods[0].bands[1]")
    assert band.method == (fmt.OWN, "home", "standard")


def test_csv_round_trips_through_its_rows() -> None:
    text = fmt.dump_csv(sample_document())
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
        "method",
        "pickup_option",
    ]


def test_a_json_method_without_a_band_list_keeps_its_bands() -> None:
    document = sample_document()
    del document["methods"][0]["bands"]  # absent: keep the stored bands
    parsed = fmt.parse_json(document)

    assert parsed.ok, parsed.issues
    # The world method lists its bands (an empty list): they replace the stored ones.
    assert parsed.band_methods == {(fmt.PLATFORM, "world", "express")}
    assert fmt.to_document(parsed) == document


def test_a_csv_method_without_band_lines_keeps_its_bands() -> None:
    parsed = fmt.parse_csv(fmt.dump_csv(sample_document()))

    assert parsed.ok, parsed.issues
    # CSV cannot write an empty list: a method without band lines keeps its stored bands.
    assert parsed.band_methods == {(fmt.OWN, "home", "standard")}
    world = fmt.to_document(parsed)["methods"][1]
    assert fmt.BANDS not in world


def test_csv_cells_become_lists_nulls_and_booleans() -> None:
    lines = fmt.dump_csv(sample_document()).splitlines()
    assert lines[0].split(",") == list(fmt.CSV_COLUMNS)
    assert lines[1].startswith(f"format,{fmt.FORMAT},{fmt.VERSION},")
    parsed = fmt.parse_csv("\n".join(lines))
    rows = {row.ref: row for row in parsed.rows}
    islands = next(row for row in parsed.rows if row.values.get("code") == "islands")
    assert islands.values["postcode_patterns"] == ["846*", "70000..74999", "BT1 1AA"]
    assert islands.ref == "line 5"
    unlimited = next(row for row in rows.values() if row.values.get("from_kg") == "5.000")
    assert unlimited.values["to_kg"] is None
    default = next(row for row in rows.values() if row.values.get("key") == "standard")
    assert default.values["is_default"] is True


def test_an_unknown_format_or_version_is_refused() -> None:
    document = sample_document()
    document["version"] = fmt.VERSION + 1
    assert messages(fmt.parse_json(document)) == [("file", "version")]
    document["format"] = "something-else"
    assert ("file", "format") in messages(fmt.parse_json(document))


def test_text_that_is_not_json_is_refused() -> None:
    parsed = fmt.parse_json("{not json")
    assert not parsed.ok
    assert messages(parsed) == [("file", "format")]


def test_an_unknown_field_is_reported_on_its_row() -> None:
    document = sample_document()
    document["zones"][1]["colour"] = "blue"
    assert messages(fmt.parse_json(document)) == [("zones[1]", "colour")]


def test_a_list_field_must_hold_text_items() -> None:
    document = sample_document()
    document["zones"][0]["countries"] = "FR"
    document["methods"][0]["bands"][0]["cost_net"] = ["9.90"]
    assert messages(fmt.parse_json(document)) == [
        ("zones[0]", "countries"),
        ("methods[0].bands[0]", "cost_net"),
    ]


def test_rows_are_identified_once_per_file() -> None:
    document = sample_document()
    document["zones"].append(copy.deepcopy(document["zones"][0]))
    document["methods"].append(copy.deepcopy(document["methods"][1]))
    del document["zones"][0]["code"]
    assert messages(fmt.parse_json(document)) == [
        ("zones[0]", "code"),
        ("methods[2]", "key"),
    ]


def test_a_zone_scope_is_own_or_platform() -> None:
    document = sample_document()
    document["methods"][0]["zone_scope"] = "elsewhere"
    assert messages(fmt.parse_json(document)) == [("methods[0]", "zone_scope")]


def test_special_areas_are_not_part_of_version_1() -> None:
    document = sample_document()
    document["version"] = 1  # SHP.14 brought them in version 2
    document["special_areas"] = [{"code": "sardinia"}]
    assert messages(fmt.parse_json(document)) == [("special_areas[0]", "special_areas")]


def test_a_csv_band_needs_its_method_in_the_file() -> None:
    lines = fmt.dump_csv(sample_document()).splitlines()
    method_line = next(i for i, line in enumerate(lines) if line.startswith("method,"))
    del lines[method_line]
    parsed = fmt.parse_csv("\n".join(lines))
    assert ("line 6", "key") in messages(parsed)  # the first band now sits on line 6


def test_a_csv_cell_that_does_not_apply_to_its_record_is_reported() -> None:
    lines = fmt.dump_csv(sample_document()).splitlines()
    header = lines[0].split(",")
    cells = lines[2].split(",")  # the country group row
    cells[header.index("cost_net")] = "5.00"
    lines[2] = ",".join(cells)
    assert messages(fmt.parse_csv("\n".join(lines))) == [("line 3", "cost_net")]


def test_a_csv_without_the_format_row_is_refused() -> None:
    lines = fmt.dump_csv(sample_document()).splitlines()
    del lines[1]
    assert ("file", "format") in messages(fmt.parse_csv("\n".join(lines)))


def test_an_empty_document_is_valid() -> None:
    parsed = fmt.parse_json(fmt.empty_document())
    assert parsed.ok
    assert parsed.rows == []


# --- Hostile files are reported, never a traceback (SHP audit chunk 5, m2)


def test_a_deeply_nested_json_file_is_a_file_level_issue() -> None:
    parsed = fmt.parse_json("[" * 100_000 + "]" * 100_000)
    assert not parsed.ok
    assert parsed.issues[0].ref == fmt.FILE


def test_a_csv_cell_over_the_csv_field_limit_is_a_file_level_issue() -> None:
    parsed = fmt.parse_csv("record,code\n" + "x" * 200_000 + ",y\n")
    assert not parsed.ok
    assert parsed.issues[0].ref == fmt.FILE


def test_a_file_over_the_size_cap_is_refused_before_parsing() -> None:
    big = " " * (fmt.MAX_FILE_BYTES + 1)
    assert not fmt.parse_json(big).ok
    assert not fmt.parse_csv(big).ok
