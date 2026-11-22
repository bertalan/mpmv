"""Reader for the help-label check: bold labels in help files against the shipped catalogs (ARC.37).

Rule: a **bold** span in a help file quotes a screen label, so it must be a msgstr of that
language's catalog (English: a msgid). Headings are section names of the help, not screen labels;
a heading that quotes a label wraps it in bold (``## **Label**``) and is checked like any span.
Emphasis that is not a screen label is not bold.
"""

import ast
import re
from pathlib import Path

from apps.cms import help_files

LANGS = ("en", "it", "de", "fr", "es")
KINDS = ("guide", "route", "page_type", "general")
_BASE = Path(__file__).resolve().parent.parent


def po_strings(lang: str) -> tuple[set[str], set[str]]:
    """The msgids and msgstrs of a catalog (minimal reader: no polib, never writes)."""
    path = _BASE / "locale" / lang / "LC_MESSAGES" / "django.po"
    ids: set[str] = set()
    strs: set[str] = set()
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        parts: dict[str, str] = {}
        key = ""
        for raw in block.splitlines():
            line = raw.strip()
            head = re.match(r"(msgid|msgstr)(?:\[0\])?\s+(\".*\")$", line)
            if head:
                key = head.group(1)
                parts[key] = ast.literal_eval(head.group(2))
            elif line.startswith('"') and key:
                parts[key] += ast.literal_eval(line)
            else:
                key = ""
        ids.add(parts.get("msgid", ""))
        strs.add(parts.get("msgstr", ""))
    return ids, strs


def unknown_labels(kinds: tuple[str, ...] = KINDS) -> list[tuple[str, str, str]]:
    """(lang, file, label) for every bold label that is not in the language's catalog."""
    bad: list[tuple[str, str, str]] = []
    for lang in LANGS:
        # English has no catalog: its labels are the msgids (read from any catalog).
        ids, strs = po_strings("it" if lang == "en" else lang)
        known = ids if lang == "en" else strs
        for kind in kinds:
            for path in sorted((help_files.HELP_ROOT / lang / kind).glob("*.md")):
                for label in re.findall(r"\*\*(.+?)\*\*", path.read_text(encoding="utf-8")):
                    if label not in known:
                        bad.append((lang, f"{kind}/{path.name}", label))
    return bad
