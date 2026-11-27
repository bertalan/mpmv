"""Publisher feed adapter (ING.9, Vendor D): a JSON feed given as bytes.

The feed is a JSON list of flat objects, or an object whose `items` is such a list. Keys map to
fields through the vendor's mapping rows, like CSV headers. Numbers are parsed as `Decimal`
(never float); `NaN` and `Infinity` are refused. The distinct keys are capped. A mapped key
whose value is a list, object or boolean is a row error. Fetching the feed is out of scope: the
SSRF-guarded client belongs to MIG.12 (`core/http.py`); the caller passes the bytes.
"""

import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Final, NoReturn

from apps.ingest.adapters import base
from apps.ingest.adapters.table import TableParser
from apps.ingest.engine import MappingEntry

CODE: Final = "publisher_feed"


def _refuse_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not allowed")


class FeedAdapter:
    code = CODE

    def __init__(self, columns: Sequence[MappingEntry], **options: object) -> None:
        self.table = TableParser(columns, **options)  # type: ignore[arg-type]

    def parse(self, data: bytes) -> base.AdapterResult:
        self.table.check_size(data)
        try:
            doc: Any = json.loads(data, parse_float=Decimal, parse_constant=_refuse_constant)
        except (ValueError, RecursionError) as exc:
            raise base.AdapterError("The feed is not valid JSON.") from exc
        items = doc.get("items") if isinstance(doc, dict) else doc
        if not isinstance(items, list) or not items:
            raise base.AdapterError("The feed has no item list.")
        if len(items) > self.table.max_rows:
            raise base.AdapterError("The file has more rows than allowed.")
        if not all(isinstance(i, dict) for i in items):
            raise base.AdapterError("Every feed item must be an object.")
        header, rows = self.table.rows_from_dicts(items)
        return self.table.parse_rows(header, rows, encoding="json")
