import pytest

from apps.ingest.adapters import base
from apps.ingest.adapters.yaml_light import YamlAdapter
from apps.ingest.engine import MappingEntry


def _col(header: str, target: str) -> MappingEntry:
    return MappingEntry(1, "other", header, (), "", None, target)


COLUMNS = [_col("code", "sku"), _col("name", "title"), _col("text", "description")]


def _adapter(**kw: object) -> YamlAdapter:
    return YamlAdapter(COLUMNS, vendor_id=1, **kw)  # type: ignore[arg-type]


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
