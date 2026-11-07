"""The shipping configuration file format, version 1 (SHP.13, spec 06 "Configuration file
format"). Exports, imports, the standard template and the SHP.5 legacy import all use it. Pure
functions, no ORM.

This module checks the shape of a file only: its format and version, the sections and the
fields each row may have, that list fields hold text, that every row is named once and that a
CSV band has its method in the file. The values (countries, postcode patterns, amounts,
choices) are validated by the models when a file is checked or imported
(`apps.shipping.config_io`), so a file, the admin and the tests share one set of validators.

A JSON document:

    {"format": "mpmv.shipping-config", "version": 1,
     "country_groups": [{"code", "countries"}],
     "zones": [{"code", "name", "priority", "countries", "country_groups", "postcode_patterns",
                "states"}],
     "methods": [{"zone_scope", "zone", "key", "carrier", "service_level", "min_delivery_days",
                  "max_delivery_days", "is_default", "free_shipping_threshold", "tax_treatment",
                  "over_max_weight", "bands": [{"from_kg", "to_kg", "cost_net", "surcharge_net"}]}],
     "pickup_options": [{"key", "cost_net", "address"}],
     "special_areas": []}

A CSV file holds the same rows, one per line, in the `CSV_COLUMNS` order; the `record` column
says what a line is, and the first line after the header is the `format` record.
"""

import csv
import io
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from apps.core.changeset import to_jsonable

from . import engine

FORMAT: Final = "mpmv.shipping-config"
VERSION: Final = 1

# Which zone a method ships to: one of the owner's zones (in the file or already stored), or a
# platform zone (vendor files only).
OWN: Final = "own"
PLATFORM: Final = "platform"
ZONE_SCOPES: Final = (OWN, PLATFORM)

COUNTRY_GROUP: Final = "country_group"
ZONE: Final = "zone"
METHOD: Final = "method"
BAND: Final = "band"
PICKUP_OPTION: Final = "pickup_option"

FIELDS: Final[Mapping[str, tuple[str, ...]]] = {
    COUNTRY_GROUP: ("code", "countries"),
    ZONE: (
        "code",
        "name",
        "priority",
        "countries",
        "country_groups",
        "postcode_patterns",
        "states",
    ),
    METHOD: (
        "zone_scope",
        "zone",
        "key",
        "carrier",
        "service_level",
        "min_delivery_days",
        "max_delivery_days",
        "is_default",
        "free_shipping_threshold",
        "tax_treatment",
        "over_max_weight",
    ),
    BAND: ("from_kg", "to_kg", "cost_net", "surcharge_net"),
    PICKUP_OPTION: ("key", "cost_net", "address"),
}
# The fields that name a row; they are required and unique within a file.
IDENTITY: Final[Mapping[str, tuple[str, ...]]] = {
    COUNTRY_GROUP: ("code",),
    ZONE: ("code",),
    METHOD: ("zone", "key"),
    PICKUP_OPTION: ("key",),
}
# A method without a key gets the model's default key.
IDENTITY_DEFAULTS: Final[Mapping[tuple[str, str], str]] = {(METHOD, "key"): engine.STANDARD}
SECTIONS: Final[Mapping[str, str]] = {
    "country_groups": COUNTRY_GROUP,
    "zones": ZONE,
    "methods": METHOD,
    "pickup_options": PICKUP_OPTION,
}
BANDS: Final = "bands"
# Reserved for the special areas of SHP.14: version 1 files keep the list empty.
SPECIAL_AREAS: Final = "special_areas"

LIST_FIELDS: Final = frozenset({"countries", "country_groups", "postcode_patterns", "states"})
BOOLEAN_FIELDS: Final = frozenset({"is_default"})
NUMBER_FIELDS: Final = frozenset(
    {
        "priority",
        "min_delivery_days",
        "max_delivery_days",
        "free_shipping_threshold",
        "from_kg",
        "to_kg",
        "cost_net",
        "surcharge_net",
    }
)

FORMAT_RECORD: Final = "format"
LIST_SEPARATOR: Final = "|"
# A CSV band names its method with these columns.
BAND_LINK: Final = ("zone_scope", "zone", "key")
CSV_COLUMNS: Final = (
    "record",
    "code",
    "version",
    "name",
    "priority",
    "countries",
    "country_groups",
    "postcode_patterns",
    "states",
    "zone_scope",
    "zone",
    "key",
    "carrier",
    "service_level",
    "min_delivery_days",
    "max_delivery_days",
    "is_default",
    "free_shipping_threshold",
    "tax_treatment",
    "over_max_weight",
    "from_kg",
    "to_kg",
    "cost_net",
    "surcharge_net",
    "address",
)
FILE: Final = "file"  # the ref of issues that concern the whole file

type MethodId = tuple[str, str, str]  # (zone_scope, zone code, method key)


@dataclass(frozen=True)
class Issue:
    """A problem with the shape of a file: where (`ref`), which field and why."""

    ref: str
    field: str
    message: str


@dataclass(frozen=True)
class Row:
    """One row of a file, its values as written (not validated yet).

    `ref` places it in the file for the report: `zones[1]` or `methods[0].bands[2]` in JSON,
    `line 7` in CSV. A band also names its method.
    """

    record: str
    ref: str
    values: Mapping[str, Any]
    method: MethodId | None = None

    @property
    def label(self) -> str:
        """The row's name for people: `eu`, `own:home/standard`, `own:home/standard 0-5 kg`."""
        if self.record == METHOD:
            return _method_label(method_id(self.values))
        if self.record == BAND and self.method is not None:
            upper = self.values.get("to_kg")
            span = f"{self.values.get('from_kg', 0)}-{'' if upper is None else upper} kg"
            return f"{_method_label(self.method)} {span}"
        return str(self.values.get("code", self.values.get("key", "")))


@dataclass
class ParsedConfig:
    rows: list[Row] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def issue(self, ref: str, name: str, message: str) -> None:
        self.issues.append(Issue(ref, name, message))


def method_id(values: Mapping[str, Any]) -> MethodId:
    """How a method row is named: its zone scope (default own), zone code and key."""
    return (
        str(values.get("zone_scope") or OWN),
        str(values.get("zone", "")),
        str(values.get("key", engine.STANDARD)),
    )


def _method_label(method: MethodId) -> str:
    scope, zone, key = method
    return f"{scope}:{zone}/{key}"


def empty_document() -> dict[str, Any]:
    """A version 1 document without rows."""
    return {
        "format": FORMAT,
        "version": VERSION,
        **{section: [] for section in SECTIONS},
        SPECIAL_AREAS: [],
    }


# --- Reading


def parse_json(source: str | bytes | Mapping[str, Any]) -> ParsedConfig:
    """The rows of a JSON document (text or already loaded), with the issues of its shape."""
    parsed = ParsedConfig()
    data: Any = source
    if isinstance(source, str | bytes):
        try:
            data = json.loads(source)
        except ValueError as exc:
            parsed.issue(FILE, "format", f"Not a JSON document: {exc}.")
            return parsed
    if not isinstance(data, Mapping):
        parsed.issue(FILE, "format", f"Not a {FORMAT} document: expected a JSON object.")
        return parsed
    _check_header(data.get("format"), data.get("version"), parsed)
    if not parsed.ok:
        return parsed
    for name in sorted(set(data) - {"format", "version", *SECTIONS, SPECIAL_AREAS}):
        parsed.issue(FILE, name, f"Unknown section {name!r}.")
    for section, record in SECTIONS.items():
        for ref, item in _items(data.get(section, []), section, parsed):
            values = dict(item)
            bands = values.pop(BANDS, []) if record == METHOD else []
            _add_row(parsed, record, ref, values)
            for band_ref, band in _items(bands, f"{ref}.{BANDS}", parsed):
                _add_row(parsed, BAND, band_ref, dict(band), method=method_id(values))
    for ref, _ in _items(data.get(SPECIAL_AREAS, []), SPECIAL_AREAS, parsed):
        _refuse_special_area(parsed, ref)
    _check_identities(parsed)
    return parsed


def _items(
    items: Any, section: str, parsed: ParsedConfig
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(items, list):
        parsed.issue(FILE, section.rsplit(".", 1)[-1], f"{section} must be a list.")
        return
    for i, item in enumerate(items):
        ref = f"{section}[{i}]"
        if isinstance(item, Mapping):
            yield ref, item
        else:
            parsed.issue(ref, section.rsplit(".", 1)[-1], "Each row must be an object.")


def parse_csv(text: str) -> ParsedConfig:
    """The rows of a CSV file, with the issues of its shape; refs are line numbers."""
    parsed = ParsedConfig()
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None or "record" not in header:
        parsed.issue(FILE, "format", f"Not a {FORMAT} CSV file: the header needs a record column.")
        return parsed
    for name in header:
        if name not in CSV_COLUMNS:
            parsed.issue(FILE, name, f"Unknown column {name!r}.")
    lines = [
        (f"line {reader.line_num}", dict(zip(header, cells, strict=False)))
        for cells in reader
        if any(cell.strip() for cell in cells)
    ]
    first = lines[0][1] if lines else {}
    if first.get("record") != FORMAT_RECORD:
        parsed.issue(
            FILE, "format", f"The first line after the header must be the {FORMAT_RECORD} record."
        )
        return parsed
    _check_header(first.get("code"), _as_int(first.get("version", "")), parsed)
    if not parsed.ok:
        return parsed
    for ref, cells in lines[1:]:
        _add_csv_row(parsed, ref, cells)
    methods = {method_id(row.values) for row in parsed.rows if row.record == METHOD}
    for row in parsed.rows:
        if row.record == BAND and row.method is not None and row.method not in methods:
            parsed.issue(
                row.ref,
                "key",
                f"No method row {_method_label(row.method)} in this file: a band belongs to "
                "a method of the same file.",
            )
    _check_identities(parsed)
    return parsed


def _as_int(text: str) -> int | str:
    return int(text) if text.strip().isdigit() else text


def _add_csv_row(parsed: ParsedConfig, ref: str, cells: Mapping[str, str]) -> None:
    record = cells.get("record", "").strip()
    if record == "special_area":
        _refuse_special_area(parsed, ref)
        return
    if record not in FIELDS:
        parsed.issue(ref, "record", f"Unknown record {record!r}.")
        return
    allowed = {*FIELDS[record], *(BAND_LINK if record == BAND else ())}
    for name, cell in cells.items():
        if name != "record" and name not in allowed and cell.strip():
            parsed.issue(ref, name, f"Does not apply to a {record} row: leave it empty.")
    values: dict[str, Any] = {}
    for name in FIELDS[record]:
        if name in cells:
            _put_cell(values, name, cells[name])
    method = None
    if record == BAND:
        link: dict[str, Any] = {}
        for name in BAND_LINK:
            _put_cell(link, name, cells.get(name, ""))
        method = method_id(link)
    _add_row(parsed, record, ref, values, method=method)


def _put_cell(values: dict[str, Any], name: str, cell: str) -> None:
    """A CSV cell as the value a JSON document would hold; an empty optional cell is left out."""
    if name in LIST_FIELDS:
        values[name] = [item.strip() for item in cell.split(LIST_SEPARATOR)] if cell.strip() else []
    elif name in BOOLEAN_FIELDS:
        if cell.strip():
            flag = cell.strip().lower()
            values[name] = {"true": True, "false": False}.get(flag, cell)
    elif name in NUMBER_FIELDS:
        values[name] = cell.strip() or None
    elif name == "zone_scope":
        if cell.strip():
            values[name] = cell.strip()
    else:
        values[name] = cell


def _check_header(format_name: Any, version: Any, parsed: ParsedConfig) -> None:
    if format_name != FORMAT:
        parsed.issue(FILE, "format", f"Not a {FORMAT} file (format {format_name!r}).")
    if version != VERSION or isinstance(version, bool):
        parsed.issue(
            FILE, "version", f"Version {version!r} is not supported: this platform reads {VERSION}."
        )


def _refuse_special_area(parsed: ParsedConfig, ref: str) -> None:
    parsed.issue(
        ref,
        SPECIAL_AREAS,
        "Special areas are not part of format version 1 (they arrive with SHP.14): keep the "
        "list empty.",
    )


def _add_row(
    parsed: ParsedConfig,
    record: str,
    ref: str,
    values: dict[str, Any],
    *,
    method: MethodId | None = None,
) -> None:
    for name, value in values.items():
        if name not in FIELDS[record]:
            parsed.issue(ref, name, f"Unknown field {name!r} for a {record} row.")
        elif name in LIST_FIELDS:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                parsed.issue(ref, name, "Must be a list of text values.")
        elif isinstance(value, list | dict):
            parsed.issue(ref, name, "Must be a single value, not a list or an object.")
    scope = values.get("zone_scope")
    if record == METHOD and scope is not None and scope not in ZONE_SCOPES:
        parsed.issue(ref, "zone_scope", f"Must be one of {', '.join(ZONE_SCOPES)}.")
    parsed.rows.append(Row(record, ref, values, method))


def _check_identities(parsed: ParsedConfig) -> None:
    """Every row is named (code, key, zone) and named once: rows refer to each other by name."""
    seen: dict[tuple[str, Any], str] = {}
    for row in parsed.rows:
        names = IDENTITY.get(row.record)
        if names is None:
            continue
        missing = [
            name
            for name in names
            if not _is_name(row.values.get(name, IDENTITY_DEFAULTS.get((row.record, name))))
        ]
        if missing:
            for name in missing:
                parsed.issue(row.ref, name, "Required text: it names the row.")
            continue
        identity = method_id(row.values) if row.record == METHOD else row.values[names[0]].strip()
        first = seen.setdefault((row.record, identity), row.ref)
        if first != row.ref:
            parsed.issue(row.ref, names[-1], f"{row.label} is already defined by {first}.")


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


# --- Writing


def to_document(parsed: ParsedConfig) -> dict[str, Any]:
    """The JSON document of a file read without issues (bands nested in their method)."""
    if not parsed.ok:
        raise ValueError("Only a file without issues becomes a document.")
    document = empty_document()
    sections = {record: section for section, record in SECTIONS.items()}
    methods: dict[MethodId, dict[str, Any]] = {}
    for row in parsed.rows:
        if row.record == BAND:
            continue
        item = dict(row.values)
        if row.record == METHOD:
            item[BANDS] = []
            methods[method_id(row.values)] = item
        document[sections[row.record]].append(item)
    for row in parsed.rows:
        if row.record == BAND and row.method is not None:
            methods[row.method][BANDS].append(dict(row.values))
    return document


def dump_json(document: Mapping[str, Any]) -> str:
    return json.dumps(to_jsonable(document), indent=2, ensure_ascii=False) + "\n"


def dump_csv(document: Mapping[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerow(_csv_line(FORMAT_RECORD, {"code": FORMAT, "version": VERSION}))
    for group in document.get("country_groups", []):
        writer.writerow(_csv_line(COUNTRY_GROUP, group))
    for zone in document.get("zones", []):
        writer.writerow(_csv_line(ZONE, zone))
    for method in document.get("methods", []):
        writer.writerow(_csv_line(METHOD, {k: v for k, v in method.items() if k != BANDS}))
        link = dict(zip(BAND_LINK, method_id(method), strict=True))
        for band in method.get(BANDS, []):
            writer.writerow(_csv_line(BAND, {**band, **link}))
    for pickup in document.get("pickup_options", []):
        writer.writerow(_csv_line(PICKUP_OPTION, pickup))
    return out.getvalue()


def _csv_line(record: str, values: Mapping[str, Any]) -> list[str]:
    return [record, *(_csv_cell(values.get(name)) for name in CSV_COLUMNS[1:])]


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return LIST_SEPARATOR.join(str(item) for item in value)
    return str(to_jsonable(value))
