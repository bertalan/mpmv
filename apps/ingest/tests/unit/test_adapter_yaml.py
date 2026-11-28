import pytest

from apps.ingest.adapters import base
from apps.ingest.adapters.yaml_light import YamlAdapter
from apps.ingest.engine import MappingEntry


def _col(header: str, target: str) -> MappingEntry:
    return MappingEntry(1, "other", header, (), "", None, target)


COLUMNS = [
    _col("code", "sku"),
    _col("name", "title"),
    _col("text", "description"),
    _col("ean", "ean"),
    _col("price", "price"),
]


def _adapter(**kw: object) -> YamlAdapter:
    return YamlAdapter(COLUMNS, vendor_id=1, **kw)


def test_dry_run_single_document() -> None:
    res = _adapter().parse(b"code: E-1\nname: Lamp\ntext: Brass\nnote: x\n")
    assert res.dry_run
    assert not res.errors
    assert res.rows[0].product.description == "Brass"
    assert res.unmapped_columns == ["note"]


def test_list_and_row_errors() -> None:
    res = _adapter().parse(b"- {code: E-1, name: Lamp}\n- {code: E-2}\n")
    assert [r.product.sku for r in res.rows] == ["E-1"]
    assert [(e.row, e.code) for e in res.errors] == [(2, "missing_title")]


def test_python_tags_are_refused() -> None:
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"code: !!python/object/apply:os.getcwd []\nname: x\n")


def test_alias_bomb_is_refused() -> None:
    bomb = b"a: &a [x, x, x, x]\nb: &b [*a, *a, *a, *a]\nc: &c [*b, *b, *b, *b]\nname: n\ncode: c\n"
    with pytest.raises(base.AdapterError):
        _adapter().parse(bomb)


def test_anchor_without_alias_is_refused_too() -> None:
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"code: &a E-1\nname: n\n")


def test_depth_size_and_shape_caps() -> None:
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"code: " + b"[" * 50 + b"]" * 50 + b"\nname: n\n")
    with pytest.raises(base.AdapterError):
        _adapter(max_bytes=5).parse(b"code: E-1\nname: n\n")
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"just a string")
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"a: [unclosed")
    with pytest.raises(base.AdapterError):
        _adapter(max_rows=1).parse(b"- {code: a, name: b}\n- {code: c, name: d}\n")


def test_leading_zero_codes_stay_text() -> None:
    res = _adapter().parse(b"code: 0123\nname: Lamp\nean: 0012345678905\nprice: 1_0\n")
    product = res.rows[0].product
    assert product.sku == "0123"
    assert product.ean == "0012345678905"
    assert [e.code for e in res.errors] == []


def test_scalars_are_not_yaml_1_1_typed() -> None:
    res = _adapter().parse(b"code: 0o17\nname: yes\nean: 2024-01-02\n")
    product = res.rows[0].product
    assert (product.sku, product.title, product.ean) == ("0o17", "yes", "2024-01-02")


def test_price_text_goes_through_explicit_parsing() -> None:
    res = _adapter().parse(
        b"- {code: a, name: b, price: '12,50'}\n- {code: c, name: d, price: x}\n"
    )
    assert [r.product.price for r in res.rows] == ["12.50"]
    assert [e.code for e in res.errors] == ["bad_price"]


def test_unique_keys_cannot_blow_up() -> None:
    doc = "".join(f"- {{code: s{n}, name: n, k{n}: 1}}\n" for n in range(300))
    with pytest.raises(base.AdapterError):
        _adapter().parse(doc.encode())


def test_nested_value_in_a_mapped_key_is_a_row_error() -> None:
    res = _adapter().parse(b"- {code: a, name: [x]}\n- {code: b, name: ok}\n")
    assert [e.code for e in res.errors] == ["unsupported_value"]
    assert len(res.rows) == 1


def test_event_cap_follows_the_row_and_column_caps() -> None:
    doc = "".join(f"- {{code: s{n}, name: n}}\n" for n in range(50))
    with pytest.raises(base.AdapterError):
        _adapter(max_rows=5, max_columns=2).parse(doc.encode())


def test_multi_document_stream_is_refused() -> None:
    """Only one document is allowed: the second must never be silently dropped or merged."""
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"code: E-1\nname: Lamp\n---\ncode: E-2\nname: Vase\n")
    with pytest.raises(base.AdapterError):
        _adapter().parse(b"---\ncode: E-1\nname: Lamp\n...\n---\ncode: E-2\nname: Vase\n")
