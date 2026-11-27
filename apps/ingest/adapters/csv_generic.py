"""Generic CSV adapter (ING.9): any vendor with a CSV export.

Header mapping goes through the vendor's `MappingTable` rows (kind `other`: key is the file's
header text, `target_value` is one of `base.FIELDS`), handed in as `MappingEntry` values. The
adapter detects the encoding (UTF-8 with or without BOM, else CP1252, else Latin-1) and the
delimiter (`,` `;` tab `|`). Cells that start with a formula character are kept as text and
never evaluated. A field over `base.FIELD_MAX_CHARS` or a malformed line is a row error.
"""

import csv
import io
from collections.abc import Iterator, Sequence
from typing import Final

from apps.ingest.adapters import base
from apps.ingest.adapters.table import BadRow, TableParser
from apps.ingest.engine import MappingEntry

CODE: Final = "csv_generic"
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


def _rows(reader: Iterator[list[str]]) -> Iterator[Sequence[str] | BadRow]:
    while True:
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error:
            yield BadRow("bad_row", "The line cannot be read as CSV (field too long or malformed).")
            continue
        yield row


class CsvAdapter:
    code = CODE

    def __init__(self, columns: Sequence[MappingEntry], **options: object) -> None:
        self.table = TableParser(columns, **options)  # type: ignore[arg-type]

    def parse(self, data: bytes) -> base.AdapterResult:
        self.table.check_size(data)
        text, encoding = decode(data)
        if not text.strip():
            raise base.AdapterError("The file is empty.")
        csv.field_size_limit(base.FIELD_MAX_CHARS)  # process-wide, set before every read
        reader = csv.reader(io.StringIO(text), delimiter=detect_delimiter(text))
        try:
            header = next(reader)
        except csv.Error as exc:
            raise base.AdapterError("The header line cannot be read.") from exc
        return self.table.parse_rows(header, _rows(reader), encoding=encoding)
