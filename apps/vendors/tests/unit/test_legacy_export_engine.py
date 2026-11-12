"""MIG.21 (MIG.18 in lane-a's wording): the per-vendor export of legacy store reviews and enquiries.

Pure rules: which rows belong to a vendor, and the CSV (header row, UTF-8, formula-injection safe).
The rows are plain dicts, as the MIG.1 extractor yields them.
"""

import csv
import io

import pytest

from apps.vendors import engine

ROW = {
    "kind": "store_review",
    "legacy_vendor_id": 7,
    "date": "2024-03-05",
    "rating": 4,
    "author_name": "Giulia Verdi",
    "author_email": "giulia@example.com",
    "subject": "Fast",
    "message": "Arrived in two days",
    "reply": "",
    "product": "",
}


def parse(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8"))))


def test_only_the_rows_of_that_legacy_vendor_are_kept() -> None:
    other = {**ROW, "legacy_vendor_id": 8, "message": "theirs"}
    assert engine.legacy_rows_of_vendor([ROW, other], 7) == [ROW]
    assert engine.legacy_rows_of_vendor([ROW, other], 8) == [other]


@pytest.mark.parametrize("bad", [None, "", "abc", True, 7.5, [7]])
def test_a_row_with_a_missing_or_malformed_vendor_belongs_to_nobody(bad: object) -> None:
    row = {**ROW, "legacy_vendor_id": bad}
    assert engine.legacy_rows_of_vendor([row], 1) == []
    assert engine.legacy_rows_of_vendor([row], 7) == []


def test_a_vendor_without_a_legacy_id_gets_no_rows() -> None:
    assert engine.legacy_rows_of_vendor([ROW, {**ROW, "legacy_vendor_id": None}], None) == []


def test_a_numeric_string_id_matches() -> None:
    assert engine.legacy_rows_of_vendor([{**ROW, "legacy_vendor_id": "7"}], 7)


def test_the_csv_has_a_header_row_and_utf8_content() -> None:
    row = {**ROW, "message": 'Caffè e perché, "ok"\nsecond line'}
    content = engine.legacy_export_csv([row])
    assert not content.startswith(b"\xef\xbb\xbf")
    table = parse(content)
    assert tuple(table[0]) == engine.LEGACY_EXPORT_COLUMNS
    assert table[1][engine.LEGACY_EXPORT_COLUMNS.index("message")] == row["message"]
    assert table[1][0] == "store_review"
    assert table[1][engine.LEGACY_EXPORT_COLUMNS.index("rating")] == "4"


def test_no_rows_still_gives_the_header_row() -> None:
    assert [tuple(r) for r in parse(engine.legacy_export_csv([]))] == [engine.LEGACY_EXPORT_COLUMNS]


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_cells_that_start_like_a_formula_are_neutralised(lead: str) -> None:
    value = f'{lead}HYPERLINK("http://x.example","go")'
    assert engine.neutralise_cell(value) == "'" + value
    row = {**ROW, "message": value, "subject": value, "author_name": value}
    table = parse(engine.legacy_export_csv([row]))
    for column in ("message", "subject", "author_name"):
        assert table[1][engine.LEGACY_EXPORT_COLUMNS.index(column)].startswith("'" + lead)


def test_other_cells_are_not_touched() -> None:
    assert engine.neutralise_cell("hello = world") == "hello = world"
    assert engine.neutralise_cell(None) == ""
    assert engine.neutralise_cell(5) == "5"


def test_counts_split_reviews_and_enquiries() -> None:
    rows = [ROW, {**ROW, "kind": "enquiry"}, {**ROW, "kind": "enquiry"}]
    assert engine.legacy_export_counts(rows) == (1, 2)
