"""Text that an agent reads but a person does not see (SEC.5 item 10, 2d). Pure, no Django.

Zero-width characters, the bidi overrides and isolates, the BOM and C0 control characters are
how an attacker hides an instruction inside a title: it reads as an ordinary word on screen and
as a command in the raw text. `strip_hidden` drops them from text that is written for machines
(`llms.txt`, the event ICS and RSS). The left-to-right and right-to-left marks stay: right-to-left
languages need them.
"""

import re

_HIDDEN = re.compile("[​-‍⁠-⁤‪-‮⁦-⁩﻿­᠎\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_hidden(text: str) -> str:
    """`text` without invisible and control characters (TAB, LF and CR are kept)."""
    return _HIDDEN.sub("", text)
