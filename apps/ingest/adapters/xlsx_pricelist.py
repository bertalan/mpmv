"""XLSX price list adapter (ING.9, Vendor B).

Reads cell values only (`data_only=True`, read-only mode): formulas are never evaluated, a
formula cell yields its cached value or nothing. Macros and external links are not loaded. The
workbook is untrusted: before openpyxl sees it, the zip members are checked for count, declared
uncompressed size (total and per member, which also bounds the shared strings) and compression
ratio, so a zip bomb is refused. Every failure of the lazy reader becomes `AdapterError`.
"""

import io
import zipfile
from collections.abc import Sequence
from typing import Final

from defusedxml.ElementTree import ParseError  # same class as the stdlib one; we only catch it
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from apps.ingest.adapters import base
from apps.ingest.adapters.table import TableParser
from apps.ingest.engine import MappingEntry

CODE: Final = "xlsx_pricelist"
MAX_MEMBERS: Final = 500
MAX_RATIO: Final = 200
RATIO_FROM_BYTES: Final = 64 * 1024


def check_zip(
    data: bytes, *, max_unzipped: int = 50 * 1024 * 1024, max_member: int = 20 * 1024 * 1024
) -> None:
    try:
        members = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise base.AdapterError("The file is not a readable XLSX workbook.") from exc
    if len(members) > MAX_MEMBERS:
        raise base.AdapterError("The workbook has too many parts.")
    total = 0
    for info in members:
        total += info.file_size
        too_dense = info.file_size > RATIO_FROM_BYTES and info.file_size > MAX_RATIO * max(
            info.compress_size, 1
        )
        if info.file_size > max_member or total > max_unzipped or too_dense:
            raise base.AdapterError("The workbook expands to more than allowed.")


class XlsxAdapter:
    code = CODE

    def __init__(
        self,
        columns: Sequence[MappingEntry],
        *,
        sheet: str | None = None,
        max_unzipped: int = 50 * 1024 * 1024,
        max_member: int = 20 * 1024 * 1024,
        **options: object,
    ) -> None:
        self.table = TableParser(columns, **options)  # type: ignore[arg-type]
        self.sheet = sheet
        self.max_unzipped = max_unzipped
        self.max_member = max_member

    def parse(self, data: bytes) -> base.AdapterResult:
        self.table.check_size(data)
        check_zip(data, max_unzipped=self.max_unzipped, max_member=self.max_member)
        width = self.table.max_columns + 1
        try:
            book = load_workbook(
                io.BytesIO(data), read_only=True, data_only=True, keep_vba=False, keep_links=False
            )
            try:
                if self.sheet is not None and self.sheet not in book.sheetnames:
                    raise base.AdapterError("The sheet is not in the workbook.")
                sheet = book[self.sheet] if self.sheet is not None else book.worksheets[0]
                rows = sheet.iter_rows(values_only=True, max_col=width)
                try:
                    header = ["" if c is None else str(c) for c in next(rows)]
                except StopIteration:
                    raise base.AdapterError("The sheet is empty.") from None
                while header and not header[-1]:
                    header.pop()  # max_col pads every row; empty trailing headers are padding
                body = [row for _, row in zip(range(self.table.max_rows + 1), rows, strict=False)]
            finally:
                book.close()
        except base.AdapterError:
            raise
        except (
            zipfile.BadZipFile,
            KeyError,
            ValueError,
            OSError,
            ParseError,
            InvalidFileException,
            IndexError,
            AttributeError,
            TypeError,
        ) as exc:
            raise base.AdapterError("The file is not a readable XLSX workbook.") from exc
        return self.table.parse_rows(header, body, encoding="xlsx")
