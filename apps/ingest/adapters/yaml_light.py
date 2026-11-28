"""YAML light adapter (ING.9, Vendor E): vendors without a website describe a product (or a
list of products) in YAML.

The YAML is untrusted. The event stream is checked first, without building any object: anchors
and aliases are refused (they allow a billion-laughs blow-up), nesting and the number of events
are capped (the event cap follows the row and column caps). Only then a `SafeLoader` that
resolves every plain scalar as text builds the data: no Python tags, and no YAML 1.1 surprises
(`0123` stays "0123", not octal 83; `1_000`, `yes`, dates stay text). The row validation then
converts numbers explicitly. A stream of several documents (`---`) is refused as a whole (the
single-document load fails), never cut to its first document.
"""

from collections.abc import Sequence
from typing import Any, Final

import yaml

from apps.ingest.adapters import base
from apps.ingest.adapters.table import TableParser
from apps.ingest.engine import MappingEntry

CODE: Final = "yaml_light"
MAX_DEPTH: Final = 8


class _TextLoader(yaml.SafeLoader):
    """SafeLoader with no implicit resolvers: every untagged scalar is a str."""


_TextLoader.yaml_implicit_resolvers = {}


def check_events(data: bytes, *, max_events: int) -> None:
    depth = 0
    try:
        for count, event in enumerate(yaml.parse(data), start=1):
            if count > max_events:
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


class YamlAdapter:
    code = CODE

    def __init__(self, columns: Sequence[MappingEntry], **options: object) -> None:
        self.table = TableParser(columns, **options)  # type: ignore[arg-type]

    def parse(self, data: bytes) -> base.AdapterResult:
        self.table.check_size(data)
        # Rows x (key + value per column) plus the document and list framing.
        max_events = self.table.max_rows * (2 * self.table.max_columns + 4) + 8
        check_events(data, max_events=max_events)
        try:
            doc: Any = yaml.load(data, Loader=_TextLoader)  # noqa: S506  (a SafeLoader subclass)
        except yaml.YAMLError as exc:  # includes the constructor error of any unsafe tag
            raise base.AdapterError("The file is not valid YAML.") from exc
        items = doc if isinstance(doc, list) else [doc]
        if len(items) > self.table.max_rows:
            raise base.AdapterError("The file has more rows than allowed.")
        if not items or not all(isinstance(i, dict) for i in items):
            raise base.AdapterError("The YAML must hold a product or a list of products.")
        header, rows = self.table.rows_from_dicts(items)
        return self.table.parse_rows(header, rows, encoding="yaml")
