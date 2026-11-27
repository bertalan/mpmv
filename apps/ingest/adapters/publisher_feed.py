"""Publisher feed adapter (ING.9, Vendor D): a JSON feed given as bytes.

The feed is a JSON list of flat objects, or an object whose `items` is such a list. Keys map to
fields through the vendor's mapping rows, like CSV headers. Fetching the feed is out of scope:
the SSRF-guarded client belongs to MIG.12 (`core/http.py`); the caller passes the bytes.
"""

import json
from collections.abc import Sequence
from typing import Any, Final

from apps.ingest.adapters import base
from apps.ingest.adapters.csv_generic import CsvAdapter

CODE: Final = "publisher_feed"


class FeedAdapter(CsvAdapter):
    code = CODE

    def parse(self, data: bytes) -> base.AdapterResult:
        if len(data) > self.max_bytes:
            raise base.AdapterError("The file is larger than the allowed size.")
        try:
            doc: Any = json.loads(data)
        except (ValueError, RecursionError) as exc:
            raise base.AdapterError("The feed is not valid JSON.") from exc
        items = doc.get("items") if isinstance(doc, dict) else doc
        if not isinstance(items, list) or not items:
            raise base.AdapterError("The feed has no item list.")
        if len(items) > self.max_rows:
            raise base.AdapterError("The file has more rows than allowed.")
        if not all(isinstance(i, dict) for i in items):
            raise base.AdapterError("Every feed item must be an object.")
        header: list[str] = list(dict.fromkeys(k for i in items for k in i))
        rows: Sequence[Sequence[object]] = [
            [i[k] if isinstance(i.get(k), str | int | float) else None for k in header]
            for i in items
        ]
        return self._parse_rows(header, rows, encoding="json")
