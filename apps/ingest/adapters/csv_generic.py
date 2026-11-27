"""Generic CSV adapter (ING.9): any vendor with a CSV export.

Header mapping goes through the vendor's `MappingTable` rows (kind `other`: key is the file's
header text, `target_value` is one of `base.FIELDS`), handed in as `MappingEntry` values. The
adapter detects the encoding (UTF-8 with or without BOM, else CP1252, else Latin-1) and the
delimiter (`,` `;` tab `|`). Cells that start with a formula character are kept as text and
never evaluated.
"""

import csv
import io
from collections.abc import Iterable, Sequence
from typing import Final

from apps.ingest.adapters import base
from apps.ingest.engine import MappingEntry, resolve_mapping

CODE: Final = "csv_generic"
COLUMN_KIND: Final = "other"
_DELIMITERS: Final = ",;\t|"
_ENCODINGS: Final = ("utf-8-sig", "cp1252", "latin-1")


def decode(data: bytes) -> tuple[str, str]:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise base.AdapterError("The file cannot be decoded.")  # pragma: no cover


def detect_delimiter(text: str) -> str:
    head = "\n".join(text.splitlines()[:5])
    try:
        return csv.Sniffer().sniff(head, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        first = text.splitlines()[0] if text.strip() else ""
        return max(_DELIMITERS, key=first.count)


class CsvAdapter:
    code = CODE

    def __init__(
        self,
        columns: Sequence[MappingEntry],
        *,
        vendor_id: int | None = None,
        max_bytes: int = base.DEFAULT_MAX_BYTES,
        max_rows: int = base.DEFAULT_MAX_ROWS,
    ) -> None:
        self.columns = tuple(columns)
        self.vendor_id = vendor_id
        self.max_bytes = max_bytes
        self.max_rows = max_rows

    def _field_for(self, header: str) -> str | None:
        entry = resolve_mapping(
            self.columns, vendor_id=self.vendor_id, kind=COLUMN_KIND, raw=header
        )
        if entry is None or entry.target_value not in base.FIELDS:
            return None
        return entry.target_value

    def parse(self, data: bytes) -> base.AdapterResult:
        if len(data) > self.max_bytes:
            raise base.AdapterError("The file is larger than the allowed size.")
        text, encoding = decode(data)
        if not text.strip():
            raise base.AdapterError("The file is empty.")
        reader = csv.reader(io.StringIO(text), delimiter=detect_delimiter(text))
        header = next(reader)
        return self._parse_rows(header, reader, encoding=encoding)

    def _parse_rows(
        self, header: Sequence[str], rows: Iterable[Sequence[object]], *, encoding: str
    ) -> base.AdapterResult:
        fields = [self._field_for(h) for h in header]
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
            if not any(c is not None and str(c).strip() for c in cells):
                continue
            values = {f: c for f, c in zip(fields, cells, strict=False) if f is not None}
            built = base.build_row(row_no, values)
            if isinstance(built, base.RowError):
                result.errors.append(built)
            elif built.product.sku.casefold() in seen:
                result.errors.append(base.RowError(row_no, "duplicate_sku", "The SKU repeats."))
            else:
                seen.add(built.product.sku.casefold())
                result.rows.append(built)
        return result
