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
