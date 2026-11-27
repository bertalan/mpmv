import pytest

from apps.ingest.adapters import base
from apps.ingest.adapters.csv_generic import CsvAdapter, decode, detect_delimiter
from apps.ingest.engine import MappingEntry


def _col(header: str, target: str, vendor_id: int | None = 1) -> MappingEntry:
    return MappingEntry(vendor_id, "other", header, (), "", None, target)


COLUMNS = [
    _col("Code", "sku"),
    _col("Name", "title"),
    _col("Price", "price"),
    _col("Qty", "stock_quantity"),
]


def _adapter(**kw: object) -> CsvAdapter:
    return CsvAdapter(COLUMNS, vendor_id=1, **kw)  # type: ignore[arg-type]


def test_parses_rows_with_mapped_headers() -> None:
    res = _adapter().parse(b'Code,Name,Price,Qty,Extra\nA-1,Pan,"1.234,50",3,x\n')
    assert res.dry_run
    assert not res.errors
    assert res.unmapped_columns == ["Extra"]
    p = res.rows[0].product
    assert (p.sku, p.title, p.price, p.stock_quantity) == ("A-1", "Pan", "1234.50", 3)
    assert res.rows[0].observation.payload["sku"] == "A-1"
    assert len(res.rows[0].observation.payload_hash) == 64


def test_semicolon_and_cp1252_detected() -> None:
    data = "Code;Name;Price\nA-1;Caffè;2,5\n".encode("cp1252")
    res = _adapter().parse(data)
    assert res.encoding == "cp1252"
    assert res.rows[0].product.title == "Caffè"
    assert res.rows[0].product.price == "2.50"


def test_bom_utf8_and_delimiters() -> None:
    assert decode(b"\xef\xbb\xbfCode,Name")[1] == "utf-8-sig"
    assert detect_delimiter("a\tb\tc\n1\t2\t3") == "\t"
    assert detect_delimiter("a|b|c\n1|2|3") == "|"


def test_row_errors_do_not_stop_the_file() -> None:
    data = (
        b"Code,Name,Price,Qty\nA-1,Pan,abc,1\n,NoSku,1,1\nA-2,Pot,1,-2\nA-3,Ok,1,1\nA-3,Dup,1,1\n\n"
    )
    res = _adapter().parse(data)
    assert [(e.row, e.code) for e in res.errors] == [
        (1, "bad_price"),
        (2, "missing_sku"),
        (3, "bad_stock"),
        (5, "duplicate_sku"),
    ]
    assert [r.product.sku for r in res.rows] == ["A-3"]
    assert res.counters()["errors"] == 4
    assert res.counters()["seen"] == 5


def test_formula_cells_stay_text() -> None:
    res = _adapter().parse(b"Code,Name\nA-1,=1+1\n")
    assert res.rows[0].product.title == "=1+1"


def test_limits_and_unusable_files_are_refused() -> None:
    with pytest.raises(base.AdapterError):
        _adapter(max_bytes=10).parse(b"Code,Name\nA-1,Pan\n")
    with pytest.raises(base.AdapterError):
        _adapter(max_rows=1).parse(b"Code,Name\nA-1,Pan\nA-2,Pot\n")
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"  \n")
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"Foo,Bar\n1,2\n")


def test_vendor_mapping_wins_over_platform() -> None:
    cols = [_col("Code", "title", vendor_id=None), _col("Code", "sku"), _col("Name", "title")]
    res = CsvAdapter(cols, vendor_id=1).parse(b"Code,Name\nA-1,Pan\n")
    assert res.rows[0].product.sku == "A-1"
