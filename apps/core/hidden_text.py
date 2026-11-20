"""Text that an agent reads but a person does not see (SEC.5 item 10, 2d). Pure, no Django.

Zero-width characters, the bidi overrides and isolates, the BOM, the Unicode tag block, the
variation selectors, the line and paragraph separators and C0 control characters are how an
attacker hides an instruction inside a title: it reads as an ordinary word on screen and
as a command in the raw text. `strip_hidden` drops them from text that is written for machines
(`llms.txt`, the event ICS and RSS). The left-to-right and right-to-left marks stay: right-to-left
languages need them.
"""

import re

_HIDDEN = re.compile(
    "["
    r"\x00-\x08\x0b\x0c\x0e-\x1f\x7f"  # C0 controls and DEL (TAB, LF, CR stay)
    r"\u00ad"  # soft hyphen
    r"\u180e"  # Mongolian vowel separator
    r"\u200b-\u200d"  # zero-width space, non-joiner, joiner
    r"\u2028\u2029"  # line and paragraph separators
    r"\u202a-\u202e"  # bidi embeddings and overrides
    r"\u2060-\u2064"  # word joiner and invisible operators
    r"\u2066-\u2069"  # bidi isolates
    r"\ufe00-\ufe0f"  # variation selectors
    r"\ufeff"  # BOM, zero-width no-break space
    r"\ufff9-\ufffb"  # interlinear annotation anchors
    r"\U000e0000-\U000e007f"  # tag block (hidden prompts)
    r"\U000e0100-\U000e01ef"  # variation selectors supplement
    "]"
)


def strip_hidden(text: str) -> str:
    """`text` without invisible and control characters (TAB, LF and CR are kept)."""
    return _HIDDEN.sub("", text)
