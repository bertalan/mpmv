"""Fields exempt from the field-help rule (ARC.30), each with the reason it is exempt.

The rule ([01](../../docs/spec/01-stack-and-architecture.md), "Field help") asks for plain help on
every editor-facing field. Only a field whose label already tells a non-technical vendor what to
type, with nothing to explain (no limit, no effect, no format), belongs here. Doubt means no:
write the help text instead. Every entry needs a reason; an empty reason fails the sweep.
"""

from __future__ import annotations

# Field name (the last part of the key, on any model, block or form) -> why it needs no help.
ALLOWED_FIELD_NAMES: dict[str, str] = {
    "title": "The label says it: the name shown for this item; nothing to limit or explain.",
}

# Full sweep key (see tests/unit/field_help_sweep.py) -> why this one field needs no help.
# Each key must exist, so an entry cannot outlive the field it exempts.
ALLOWED_KEYS: dict[str, str] = {}
