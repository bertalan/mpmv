"""The row-mapping core every tabular adapter composes (CSV, XLSX, feed, YAML): limits, header
mapping through `MappingEntry` rows, per-row validation and the report."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from apps.ingest.adapters import base
from apps.ingest.engine import AmbiguousMapping, MappingEntry, resolve_mapping

COLUMN_KIND = "other"  # MappingTable kind that carries column mappings (no new kind: no migration)


@dataclass(frozen=True)
class BadRow:
    """A row the reader could not even produce; reported, never raised."""

    code: str
    message: str


class TableParser:
    def __init__(
        self,
        columns: Sequence[MappingEntry],
        *,
        vendor_id: int | None = None,
        max_bytes: int = base.DEFAULT_MAX_BYTES,
        max_rows: int = base.DEFAULT_MAX_ROWS,
        max_columns: int = base.DEFAULT_MAX_COLUMNS,
        decimal: str | None = None,
    ) -> None:
        self.columns = tuple(columns)
        self.vendor_id = vendor_id
        self.max_bytes = max_bytes
        self.max_rows = max_rows
        self.max_columns = max_columns
        self.decimal = decimal

    def check_size(self, data: bytes) -> None:
        if len(data) > self.max_bytes:
            raise base.AdapterError("The file is larger than the allowed size.")

    def field_for(self, header: str) -> str | None:
        try:
            entry = resolve_mapping(
                self.columns, vendor_id=self.vendor_id, kind=COLUMN_KIND, raw=header
            )
        except AmbiguousMapping as exc:
            raise base.AdapterError(f"Two mappings answer the column {header!r}.") from exc
        if entry is None or entry.target_value not in base.FIELDS:
            return None
        return entry.target_value

    def parse_rows(
        self, header: Sequence[str], rows: Iterable[Sequence[object] | BadRow], *, encoding: str
    ) -> base.AdapterResult:
        if len(header) > self.max_columns:
            raise base.AdapterError("The file has more columns than allowed.")
        fields = [self.field_for(h) for h in header]
        result = base.AdapterResult(encoding=encoding)
        result.unmapped_columns = [
            h for h, f in zip(header, fields, strict=True) if f is None and h.strip()
        ]
        missing = [n for n in base.REQUIRED_FIELDS if n not in fields]
        if missing:
            raise base.AdapterError(f"No column is mapped to: {', '.join(missing)}.")
        seen: set[str] = set()
        for row_no, cells in enumerate(rows, start=1):
            if row_no > self.max_rows:
                raise base.AdapterError("The file has more rows than allowed.")
            if isinstance(cells, BadRow):
                result.errors.append(base.RowError(row_no, cells.code, cells.message))
                continue
            if not any(c is not None and str(c).strip() for c in cells):
                continue
            values = {f: c for f, c in zip(fields, cells, strict=False) if f is not None}
            built = base.build_row(row_no, values, decimal=self.decimal)
            if isinstance(built, base.RowError):
                result.errors.append(built)
            elif built.product.sku.casefold() in seen:
                result.errors.append(base.RowError(row_no, "duplicate_sku", "The SKU repeats."))
            else:
                seen.add(built.product.sku.casefold())
                result.rows.append(built)
        return result

    def rows_from_dicts(
        self, items: Sequence[Mapping[Any, Any]]
    ) -> tuple[list[str], list[Sequence[object] | BadRow]]:
        """Header and rows of a list of flat objects (feed, YAML). The distinct keys are capped
        while collecting, so unique keys per item cannot blow the table up."""
        keys: dict[str, str | None] = {}
        for item in items:
            for key in item:
                name = str(key)
                if name not in keys:
                    if len(keys) >= self.max_columns:
                        raise base.AdapterError("The file has more columns than allowed.")
                    keys[name] = self.field_for(name)
        header = list(keys)
        rows: list[Sequence[object] | BadRow] = []
        for item in items:
            row: list[object] = []
            bad = ""
            for name in header:
                value = item.get(name)
                if value is None or (
                    isinstance(value, str | int | float | Decimal) and not isinstance(value, bool)
                ):
                    row.append(value)
                else:
                    row.append(None)
                    if keys[name] is not None:
                        bad = name
            if bad:
                rows.append(BadRow("unsupported_value", f"The value of {bad!r} is not a scalar."))
            else:
                rows.append(row)
        return header, rows
