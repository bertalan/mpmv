"""SEC.5 item 8: hostile input and wild copy-paste against the pure parsers and renderers.

The corpus and strategies live in `tests.hostile_input`. Every test here asserts one of: the
value is refused with the parser's own error (never an unexpected exception), the value is
normalised, or the output keeps it inert (escaped HTML and JSON-LD, CSV cells that are not
formulas, single-line email headers, filenames without a path).
"""

import contextlib
import csv
import io
import json
import re
import secrets
import unicodedata
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from apps.cms.engine import faq_page_jsonld, jsonld_text
from apps.cms.templatetags.cms_tags import faq_jsonld
from apps.core import mail
from apps.core.geocoding import engine as geo
from apps.core.postcodes import InvalidPostcodePattern, normalize_postcode, parse_postcode_pattern
from apps.shipping import config_format as fmt
from apps.vendors import engine as vendors
from tests import hostile_input as hi

WEBHOOK_KEY = secrets.token_urlsafe()

# --- The corpus itself ----------------------------------------------------------------------


def test_the_corpus_has_every_category_the_owner_listed() -> None:
    assert set(hi.CORPUS) >= {
        "html", "template", "sql", "shell", "markdown", "word_html",
        "control", "zero_width", "bidi", "combining", "emoji", "homoglyphs",
        "lone_surrogates", "non_nfc", "prices", "dates", "vat_numbers", "ibans", "postcodes",
        "json_text", "csv_formulas", "header_injection", "filenames",
    }  # fmt: skip
    assert all(hi.CORPUS.values())
    assert any("\x00" in value for value in hi.CONTROL)
    assert any(hi.has_lone_surrogate(value) for value in hi.LONE_SURROGATES)
    assert all(unicodedata.normalize("NFC", value) != value for value in hi.NON_NFC)
    assert "1.234,50 €" in hi.PRICES


def test_at_and_over_gives_the_limit_and_one_more() -> None:
    exact, over = hi.at_and_over(5)
    assert (len(exact), len(over)) == (5, 6)


def test_the_detectors_see_what_a_browser_or_a_spreadsheet_would_run() -> None:
    assert hi.active_markup("<script>alert(1)</script>") == ["script element"]
    assert "event handler" in hi.active_markup("<img src=x onerror=alert(1)>")
    assert "javascript: URL" in hi.active_markup('<a href="javascript:alert(1)">x</a>')
    assert hi.active_markup("&lt;script&gt;alert(1)&lt;/script&gt;") == []
    assert all(hi.formula_cell(cell) for cell in hi.CSV_FORMULAS)
    assert not hi.formula_cell("'=1+1")
    assert not any(hi.header_safe(value) for value in hi.HEADER_INJECTION)


@given(hi.hostile_text())
def test_the_strategy_yields_text(value: str) -> None:
    assert isinstance(value, str)


# --- JSON-LD (spec 10) ----------------------------------------------------------------------


@given(hi.hostile_text(), hi.hostile_text())
def test_json_ld_text_cannot_leave_its_script_element(question: str, answer: str) -> None:
    text = jsonld_text(faq_page_jsonld([(question, answer)]))

    assert not re.search(r"[<>&]", text)
    assert json.loads(text)["mainEntity"][0]["name"] == question


@pytest.mark.parametrize("value", hi.values("html", "template", "bidi", "lone_surrogates"))
def test_the_faq_tag_renders_one_inert_script_element(value: str) -> None:
    item = {"question": value, "answer": value}
    page = SimpleNamespace(body=[SimpleNamespace(block_type="faq", value={"items": [item]})])

    html = str(faq_jsonld(page))

    assert html.count("<script") == 1
    assert html.count("</script>") == 1
    inner = html.removeprefix('<script type="application/ld+json">').removesuffix("</script>")
    assert json.loads(inner)["mainEntity"][0]["name"] == value


# --- Email (ARC.16) -------------------------------------------------------------------------


@pytest.mark.parametrize("html", hi.values("html", "word_html"))
def test_the_email_html_linter_refuses_pasted_active_markup(html: str) -> None:
    if hi.active_markup(html) and not re.search(r"<\s*(form|base|meta)\b", html, re.I):
        assert mail.html_violations(f"<p>{html}</p>")


@given(hi.hostile_text())
def test_the_feedback_parser_refuses_anything_but_a_feedback_payload(body: str) -> None:
    parser = mail.SignedJsonFeedback(WEBHOOK_KEY)
    try:
        events = parser.parse(body.encode("utf-8", "surrogatepass"))
    except mail.InvalidFeedback:
        return
    assert all(hi.header_safe(event.recipient) or "@" in event.recipient for event in events)


@given(hi.json_values())
def test_the_feedback_parser_refuses_odd_event_shapes(value: Any) -> None:
    parser = mail.SignedJsonFeedback(WEBHOOK_KEY)
    for event in (value, {"type": value, "recipient": "a@example.com"}, {"recipient": value}):
        body = json.dumps({"events": [event]}).encode()
        with contextlib.suppress(mail.InvalidFeedback):
            parser.parse(body)


@pytest.mark.parametrize("value", hi.values("json_text"))
def test_the_feedback_parser_refuses_malformed_and_deep_json(value: str) -> None:
    with pytest.raises(mail.InvalidFeedback):
        mail.SignedJsonFeedback(WEBHOOK_KEY).parse(value.encode("utf-8", "surrogatepass"))


# --- Shipping configuration files (SHP.13): import parsers and the CSV export ---------------


def _document_with(text: str) -> dict[str, Any]:
    return {
        "format": fmt.FORMAT,
        "version": fmt.VERSION,
        "country_groups": [],
        "zones": [
            {
                "code": "home",
                "name": text,
                "priority": 1,
                "countries": ["FR"],
                "country_groups": [],
                "postcode_patterns": [text],
                "states": [],
            }
        ],
        "methods": [
            {
                "zone": "home",
                "zone_scope": fmt.OWN,
                "key": "standard",
                "carrier": text,
                "service_level": "standard",
                "tax_treatment": "follows_goods",
                "over_max_weight": "",
                "bands": [{"from_kg": "0", "to_kg": None, "cost_net": "-1", "surcharge_net": "0"}],
            }
        ],
        "pickup_options": [{"key": "shop", "cost_net": "0.00", "address": text}],
        "special_areas": [],
    }


@pytest.mark.parametrize("formula", hi.CSV_FORMULAS)
def test_the_csv_export_never_writes_a_formula_cell(formula: str) -> None:
    text = fmt.dump_csv(_document_with(formula))

    cells = [cell for row in csv.reader(io.StringIO(text)) for cell in row]
    assert not [cell for cell in cells if hi.formula_cell(cell) and not _number(cell)]


@pytest.mark.parametrize("formula", hi.CSV_FORMULAS)
def test_an_escaped_formula_cell_reads_back_as_written(formula: str) -> None:
    parsed = fmt.parse_csv(fmt.dump_csv(_document_with(formula)))

    zone = next(row for row in parsed.rows if row.record == fmt.ZONE)
    assert zone.values["name"] == formula


def test_the_formula_detector_looks_past_blanks_and_folds_full_width_signs() -> None:
    assert all(hi.formula_cell(cell) for cell in (" =1", "\xa0+1", "\n-1", "\uff1d1", "\ufe661"))
    assert not any(hi.formula_cell(cell) for cell in ("'=1+1", "' =1", "1+1", " abc", "", "  "))


def test_the_ci_hypothesis_profile_cannot_flake_on_a_deadline() -> None:
    from hypothesis import settings

    profile = settings.get_profile("ci")
    assert profile.deadline is None
    assert profile.derandomize
    assert profile.database is None


def _number(cell: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", cell))


@example("[" * fmt.MAX_CELL_CHARS)
@example("[" * 100_000 + "]" * 100_000)  # the nightly falsifying example: past csv's field limit
@given(hi.storable_text(max_size=60).filter(lambda v: v.strip() and "|" not in v))
def test_the_csv_export_round_trips_escaped_cells(text: str) -> None:
    text = text.replace("\r", "").replace("\n", " ").strip() or "x"
    document = _document_with(text)
    if len(text) > fmt.MAX_CELL_CHARS:
        with pytest.raises(ValueError, match="larger than"):
            fmt.dump_csv(document)
        return

    parsed = fmt.parse_csv(fmt.dump_csv(document))

    zone = next(row for row in parsed.rows if row.record == fmt.ZONE)
    method = next(row for row in parsed.rows if row.record == fmt.METHOD)
    assert zone.values["name"] == text
    assert method.values["carrier"] == text


def test_a_cell_the_csv_reader_cannot_read_is_refused_at_export() -> None:
    with pytest.raises(ValueError, match="larger than"):
        fmt.dump_csv(_document_with("[" * (fmt.MAX_CELL_CHARS + 1)))


@given(hi.hostile_text(max_size=2000))
def test_the_csv_parser_reports_issues_instead_of_raising(text: str) -> None:
    parsed = fmt.parse_csv(text)
    assert isinstance(parsed.ok, bool)


@given(hi.hostile_text(max_size=2000))
def test_the_json_parser_reports_issues_instead_of_raising(text: str) -> None:
    parsed = fmt.parse_json(text)
    assert isinstance(parsed.ok, bool)


@pytest.mark.parametrize("text", hi.JSON_TEXT)
def test_malformed_and_nested_json_is_a_file_issue(text: str) -> None:
    parsed = fmt.parse_json(text)
    assert not parsed.ok


@given(hi.json_values())
def test_any_json_shape_in_a_section_is_reported_not_raised(value: Any) -> None:
    document = {"format": fmt.FORMAT, "version": fmt.VERSION, "zones": value, "methods": [value]}
    parsed = fmt.parse_json(document)
    assert isinstance(parsed.ok, bool)


@given(hi.hostile_text())
def test_a_hostile_csv_cell_in_every_column_is_reported_not_raised(cell: str) -> None:
    header = ",".join(fmt.CSV_COLUMNS)
    first = fmt.dump_csv(_document_with("x")).splitlines()[1]
    row = ",".join(json.dumps(cell) if i else "zone" for i, _ in enumerate(fmt.CSV_COLUMNS))
    parsed = fmt.parse_csv(f"{header}\n{first}\n{row}\n")
    assert isinstance(parsed.ok, bool)


# --- Identifiers: postcodes, VAT numbers, IBANs, coordinates --------------------------------


@given(hi.hostile_text())
def test_a_postcode_pattern_is_parsed_or_refused_with_its_own_error(value: str) -> None:
    with contextlib.suppress(InvalidPostcodePattern):
        parse_postcode_pattern(value)
    assert normalize_postcode(value) == normalize_postcode(value).upper()


@pytest.mark.parametrize("value", hi.POSTCODES)
def test_pasted_postcodes_normalise_without_spaces_or_dashes(value: str) -> None:
    assert not re.search(r"[\s-]", normalize_postcode(value))


@given(hi.hostile_text())
def test_vat_and_iban_checks_answer_yes_or_no(value: str) -> None:
    assert isinstance(vendors.is_valid_italian_vat(value), bool)
    assert isinstance(vendors.is_valid_iban(value), bool)
    assert isinstance(vendors.is_valid_codice_fiscale(value), bool)
    vendors.split_vat_number(value, "IT")


def test_pasted_ibans_with_spaces_and_lowercase_are_normalised() -> None:
    valid = "IT60X0542811101000000123456"
    assert vendors.is_valid_iban(valid.lower())
    assert vendors.is_valid_iban(" ".join(re.findall(".{1,4}", valid)))
    assert not vendors.is_valid_iban(".".join(re.findall(".{1,4}", valid)))  # refused


def test_identifiers_with_non_ascii_digits_are_refused() -> None:
    arabic = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    assert not vendors.is_valid_iban("IT60X0542811101000000123456".translate(arabic))
    assert not vendors.is_valid_italian_vat("01234567897".translate(arabic))


@given(st.one_of(hi.hostile_text(), st.sampled_from(hi.PRICES)))
def test_a_coordinate_is_a_bounded_decimal_or_a_value_error(value: str) -> None:
    try:
        number = geo.coordinate(value)
    except ValueError:
        return
    assert isinstance(number, Decimal)
    assert abs(number) <= 90
