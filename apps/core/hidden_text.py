"""Text that an agent reads but a person does not see (SEC.5 item 10, 2d). Pure, no Django.

Zero-width characters, bidi overrides and isolates, the BOM, the Unicode tag block, variation
selectors, line and paragraph separators and control characters are how an attacker hides an
instruction inside a title: it reads as an ordinary word on screen and as a command in the raw
text. `strip_hidden` drops every Unicode format (Cf) and control (Cc) character, plus a few
others, from text that is written for machines (`llms.txt`, the event ICS and RSS). It is for
agent-facing output only, not for text shown to people. LRM, RLM and VS16 stay.

The Unicode tag block U+E0000-U+E007F is always stripped, assigned or not: its unassigned
code points are category Cn, which the category sweep does not catch, yet they are a hidden
prompt vector.

Private use (category Co, for example U+E000) is NOT stripped: those characters render
visibly (icon fonts) and are not invisible.

Dependency: Cf and Cc membership comes from the running Python's `unicodedata` (see
`unicodedata.unidata_version`). A character can change category between Unicode versions, so
the stripped set can differ by Python version. Only the explicit `_EXTRA` ranges are fixed.
"""

import unicodedata

# Kept although they are format or control characters: TAB, LF, CR (layout), LRM and RLM
# (right-to-left languages need them) and VS16 (U+FE0F, emoji presentation: not an injection
# channel). The other variation selectors, the Mongolian ones, U+2028 and U+2029 are not Cf or
# Cc but hide text too, so they are listed.
_KEPT = frozenset("\t\n\r\u200e\u200f\ufe0f")
_EXTRA = frozenset(
    [
        0x2028,
        0x2029,
        *range(0x180B, 0x180E),
        *range(0xFE00, 0xFE0F),
        *range(0xE0000, 0xE0080),
        *range(0xE0100, 0xE01F0),
    ]
)


def _build_table() -> dict[int, None]:
    """Code points to delete, from `unicodedata`. Cf and Cc only occur in planes 0, 1 and 14."""
    table: dict[int, None] = {}
    for cp in (*range(0x20000), *range(0xE0000, 0xE1000)):
        if chr(cp) in _KEPT:
            continue
        if cp in _EXTRA or unicodedata.category(chr(cp)) in ("Cf", "Cc"):
            table[cp] = None
    return table


_TABLE = _build_table()  # built once at import


def strip_hidden(text: str) -> str:
    """`text` without invisible and control characters (TAB, LF, CR, LRM, RLM and VS16 stay)."""
    return text.translate(_TABLE)
