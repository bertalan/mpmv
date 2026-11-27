"""XLSX price list adapter (ING.9, Vendor B).

Reads cell values only (`data_only=True`, read-only mode): formulas are never evaluated, a
formula cell yields its cached value or nothing. Macros are not loaded (`keep_vba` off). The
workbook is untrusted: size, sheet-row and zip errors become `AdapterError`.
"""

import io
import zipfile
from collections.abc import Sequence
from typing import Final

from openpyxl import load_workbook

from apps.ingest.adapters import base
from apps.ingest.adapters.csv_generic import CsvAdapter
from apps.ingest.engine import MappingEntry

CODE: Final = "xlsx_pricelist"


class XlsxAdapter(CsvAdapter):
    code = CODE

    def __init__(
        self, columns: Sequence[MappingEntry], *, sheet: str | None = None, **kwargs: object
    ) -> None:
        super().__init__(columns, **kwargs)  # type: ignore[arg-type]
        self.sheet = sheet

    def parse(self, data: bytes) -> base.AdapterResult:
        if len(data) > self.max_bytes:
            raise base.AdapterError("The file is larger than the allowed size.")
        try:
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_vba=False)
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise base.AdapterError("The file is not a readable XLSX workbook.") from exc
        try:
            if self.sheet is not None and self.sheet not in book.sheetnames:
                raise base.AdapterError("The sheet is not in the workbook.")
            sheet = book[self.sheet] if self.sheet is not None else book.worksheets[0]
            rows = sheet.iter_rows(values_only=True)
            try:
                header = ["" if c is None else str(c) for c in next(rows)]
            except StopIteration:
                raise base.AdapterError("The sheet is empty.") from None
            body = [
                tuple(c for c in row)
                for _, row in zip(range(self.max_rows + 1), rows, strict=False)
            ]
        finally:
            book.close()
        return self._parse_rows(header, body, encoding="xlsx")
