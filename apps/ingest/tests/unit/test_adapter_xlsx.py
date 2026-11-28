import datetime
import io

import pytest
from openpyxl import Workbook

from apps.ingest.adapters import base
from apps.ingest.adapters.xlsx_pricelist import XlsxAdapter
from apps.ingest.engine import MappingEntry


def _col(header: str, target: str) -> MappingEntry:
    return MappingEntry(1, "other", header, (), "", None, target)


COLUMNS = [
    _col("Ref", "sku"),
    _col("Item", "title"),
    _col("Net", "price"),
    _col("Stock", "stock_quantity"),
]


def _book(rows: list[list[object]], *, sheet: str = "List") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_dry_run_reads_rows_and_reports_errors() -> None:
    data = _book(
        [
            ["Ref", "Item", "Net", "Stock", "Notes"],
            ["B-1", "Pan", 12.5, 4, "x"],
            ["B-2", "Pot", "n/a", 1, ""],
            [None] * 5,
        ]
    )
    res = XlsxAdapter(COLUMNS, vendor_id=1).parse(data)
    assert res.dry_run
    assert [r.product.price for r in res.rows] == ["12.50"]
    assert [(e.row, e.code) for e in res.errors] == [(2, "bad_price")]
    assert res.unmapped_columns == ["Notes"]


def test_formulas_are_not_evaluated() -> None:
    data = _book([["Ref", "Item"], ["B-1", "=1+1"]])
    res = XlsxAdapter(COLUMNS, vendor_id=1).parse(data)
    # No cached value in a file written by a library: the cell is empty, never "2".
    assert not res.rows
    assert [e.code for e in res.errors] == ["missing_title"]


def test_sheet_choice_and_limits() -> None:
    data = _book([["Ref", "Item"], ["B-1", "Pan"]], sheet="Prices")
    assert XlsxAdapter(COLUMNS, vendor_id=1, sheet="Prices").parse(data).rows
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1, sheet="Missing").parse(data)
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1, max_bytes=10).parse(data)
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1).parse(b"not a workbook")
    many = _book([["Ref", "Item"], ["B-1", "a"], ["B-2", "b"]])
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1, max_rows=1).parse(many)


def _rewrite(data: bytes, member: str, content: bytes) -> bytes:
    import zipfile

    out = io.BytesIO()
    deflated = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", deflated) as dst:
        for info in src.infolist():
            dst.writestr(info.filename, content if info.filename == member else src.read(info))
        if member not in src.namelist():
            dst.writestr(member, content)
    return out.getvalue()


def test_zip_bomb_is_refused_before_openpyxl() -> None:
    book = _book([["Ref", "Item"], ["B-1", "Pan"]])
    data = _rewrite(book, "xl/sharedStrings.xml", b"0" * 3_000_000)
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1, max_member=1_000_000).parse(data)
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1).parse(data)
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1, max_unzipped=2_000_000).parse(data)


def test_broken_sheet_xml_is_a_file_error() -> None:
    book = _book([["Ref", "Item"], ["B-1", "Pan"]])
    data = _rewrite(book, "xl/worksheets/sheet1.xml", b"<broken")
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1).parse(data)


def test_column_cap() -> None:
    data = _book([["Ref", "Item", "Net", "Stock", "More"], ["B-1", "Pan", 1, 1, 1]])
    with pytest.raises(base.AdapterError):
        XlsxAdapter(COLUMNS, vendor_id=1, max_columns=3).parse(data)


def test_float_cell_is_not_an_ambiguous_separator() -> None:
    res = XlsxAdapter(COLUMNS, vendor_id=1).parse(
        _book([["Ref", "Item", "Net"], ["B-1", "Pan", 1.234]])
    )
    assert res.rows[0].product.price == "1.23"


def test_date_cell_in_the_price_column_is_a_bad_price() -> None:
    res = XlsxAdapter(COLUMNS, vendor_id=1).parse(
        _book(
            [
                ["Ref", "Item", "Net"],
                ["B-1", "Pan", datetime.date(2024, 1, 5)],
                ["B-2", "Pot", datetime.datetime(2024, 1, 5, 10, 30)],
                ["B-3", "Lid", 3],
            ]
        )
    )
    assert [r.product.sku for r in res.rows] == ["B-3"]
    assert [(e.row, e.code) for e in res.errors] == [(1, "bad_price"), (2, "bad_price")]
