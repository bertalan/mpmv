"""YAML light adapter (ING.9, Vendor E): vendors without a website describe a product (or a
list of products) in YAML.

The YAML is untrusted. The event stream is checked first, without building any object: anchors
and aliases are refused (they allow a billion-laughs blow-up), nesting is capped, and the
event count is capped. Only then `yaml.safe_load` builds plain data (no Python tags, ever).
"""

from typing import Any, Final

import yaml

from apps.ingest.adapters import base
from apps.ingest.adapters.csv_generic import CsvAdapter

CODE: Final = "yaml_light"
MAX_DEPTH: Final = 8
MAX_EVENTS: Final = 200_000


def check_events(data: bytes) -> None:
    depth = 0
    try:
        for count, event in enumerate(yaml.parse(data), start=1):
            if count > MAX_EVENTS:
                raise base.AdapterError("The YAML is too large.")
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise base.AdapterError("YAML anchors and aliases are not allowed.")
            if isinstance(event, yaml.CollectionStartEvent):
                depth += 1
                if depth > MAX_DEPTH:
                    raise base.AdapterError("The YAML is nested too deeply.")
            elif isinstance(event, yaml.CollectionEndEvent):
                depth -= 1
    except yaml.YAMLError as exc:
        raise base.AdapterError("The file is not valid YAML.") from exc


class YamlAdapter(CsvAdapter):
    code = CODE

    def parse(self, data: bytes) -> base.AdapterResult:
        if len(data) > self.max_bytes:
            raise base.AdapterError("The file is larger than the allowed size.")
        check_events(data)
        try:
            doc: Any = yaml.safe_load(data)
        except yaml.YAMLError as exc:  # includes constructor errors for unsafe tags
            raise base.AdapterError("The file is not valid YAML.") from exc
        items = doc if isinstance(doc, list) else [doc]
        if len(items) > self.max_rows:
            raise base.AdapterError("The file has more rows than allowed.")
        if not items or not all(isinstance(i, dict) for i in items):
            raise base.AdapterError("The YAML must hold a product or a list of products.")
        header = list(dict.fromkeys(str(k) for i in items for k in i))
        rows = [
            [v if isinstance(v := i.get(k), str | int | float) else None for k in header]
            for i in items
        ]
        return self._parse_rows(header, rows, encoding="yaml")
