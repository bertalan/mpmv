import json

import pytest

from apps.ingest.adapters import base
from apps.ingest.adapters.publisher_feed import FeedAdapter
from apps.ingest.engine import MappingEntry


def _col(header: str, target: str) -> MappingEntry:
    return MappingEntry(1, "other", header, (), "", None, target)


COLUMNS = [
    _col("id", "sku"),
    _col("name", "title"),
    _col("cost", "price"),
    _col("qty", "stock_quantity"),
]


def _adapter(**kw: object) -> FeedAdapter:
    return FeedAdapter(COLUMNS, vendor_id=1, **kw)


def test_dry_run_reads_items_and_reports_errors() -> None:
    feed = {
        "items": [
            {"id": "D-1", "name": "Pan", "cost": 9.5, "qty": 2, "extra": 1},
            {"id": "D-2", "name": "Pot", "cost": "x", "qty": 1},
            {"id": "D-3", "name": "Lid", "cost": "3,20", "qty": 5},
        ]
    }
    res = _adapter().parse(json.dumps(feed).encode())
    assert res.dry_run
    assert [r.product.price for r in res.rows] == ["9.50", "3.20"]
    assert [(e.row, e.code) for e in res.errors] == [(2, "bad_price")]
    assert res.unmapped_columns == ["extra"]


def test_top_level_list_is_accepted() -> None:
    res = _adapter().parse(b'[{"id": "D-1", "name": "Pan"}]')
    assert len(res.rows) == 1


def test_refused_inputs() -> None:
    for data in (b"not json", b'{"items": 3}', b'{"items": [1]}', b"[]", b'{"a": 1}'):
        with pytest.raises(base.AdapterError):
            _adapter().parse(data)
    with pytest.raises(base.AdapterError):
        _adapter(max_bytes=5).parse(b'[{"id": "D-1", "name": "Pan"}]')
    with pytest.raises(base.AdapterError):
        _adapter(max_rows=1).parse(b'[{"id":"a","name":"b"},{"id":"c","name":"d"}]')


def test_nan_and_infinity_are_refused() -> None:
    for data in (b'[{"id":"a","name":"b","cost":NaN}]', b'[{"id":"a","name":"b","cost":Infinity}]'):
        with pytest.raises(base.AdapterError):
            _adapter().parse(data)


def test_floats_are_decimals_not_binary() -> None:
    res = _adapter().parse(b'[{"id":"a","name":"b","cost":2.675}]')
    assert res.rows[0].product.price == "2.68"


def test_unsupported_values_are_row_errors_only_for_mapped_keys() -> None:
    feed = (
        b'[{"id":"a","name":"b","cost":[1]},{"id":"c","name":"d","tags":["x"]},'
        b'{"id":"e","name":true}]'
    )
    res = _adapter().parse(feed)
    assert [r.product.sku for r in res.rows] == ["c"]
    assert [(e.row, e.code) for e in res.errors] == [
        (1, "unsupported_value"),
        (3, "unsupported_value"),
    ]


def test_unique_keys_per_item_cannot_blow_up() -> None:
    items = [{"id": f"s{n}", "name": "n", f"k{n}": 1} for n in range(300)]
    with pytest.raises(base.AdapterError):
        _adapter().parse(json.dumps(items).encode())
    assert _adapter(max_columns=400).parse(json.dumps(items).encode()).rows


def test_huge_number_is_a_row_error() -> None:
    res = _adapter().parse(b'[{"id":"a","name":"b","cost":1e999999}]')
    assert [e.code for e in res.errors] == ["bad_price"]
