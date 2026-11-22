"""British spelling of the English interface strings (I18N.23).

    python tools/i18n_british.py check   # exit 1 when an EN msgid uses a word from US_TO_UK

The English msgids are the English interface, and it is written in British English. The check
scans the msgids the gettext catalog collects (`tools/i18n_catalog.py collect`) whose source file
is in an allow-listed app and fails on a word from the fixed list below. It never looks at code
identifiers, URL names or model and field names: only at msgids. The list grows slice by slice
(I18N.25 made it every app and theme); a word that must stay American goes to EXCEPTIONS with
the reason.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Every app is swept (I18N.25): paths are matched on the source reference of the msgid.
ALLOWED_APPS = ("apps/", "themes/")

# American word (regex, whole word, case-insensitive) -> British spelling to use instead.
US_TO_UK: dict[str, str] = {
    r"colors?": "colour",
    r"colored": "coloured",
    r"catalogs?": "catalogue",
    r"organiz(?:e|es|ed|ing)": "organise, organises, organised, organising",
    r"organization": "organisation",
    r"centers?|centered": "centre",
    r"favorites?": "favourite",
    r"canceled|canceling": "cancelled, cancelling",
    r"fulfill(?:s|ed|ing|ment)?": "fulfil, fulfilled, fulfilment",
    r"behaviors?": "behaviour",
    r"authoriz\w+": "authoris-",
    r"apologiz\w+": "apologis-",
    r"customiz\w+": "customis-",
    r"initializ\w+": "initialis-",
    r"normaliz\w+": "normalis-",
    r"summariz\w+": "summaris-",
    r"synchroniz\w+": "synchronis-",
    r"visualiz\w+": "visualis-",
    r"optimiz\w+": "optimis-",
    r"utiliz\w+": "utilis-",
    r"categoriz\w+": "categoris-",
    r"prioritiz\w+": "prioritis-",
    r"finaliz\w+": "finalis-",
    r"analyz\w+": "analys-",
    r"recogniz\w+": "recognis-",
    r"neighbors?": "neighbour",
    r"honors?": "honour",
    r"gray": "grey",
    r"defense": "defence",
    r"labeled|labeling": "labelled, labelling",
    r"jewelry": "jewellery",
    r"enrollment": "enrolment",
    r"judgment": "judgement",
}

# Exact msgids that keep a listed word, with the reason.
EXCEPTIONS: dict[str, str] = {
    # `catalog:brand` is a URL name, an identifier the editor types; it is never renamed.
    "The name of the screen (for example catalog:brand) or of the page type (for example "
    "cms.StandardPage). Leave it empty for the general text.": "URL name",
    # "Catalog and moderation" is the name of a role group stored in the database (migration
    # 0003); renaming it needs a data migration of its own, so the notice keeps naming it.
    "You can view this section; changes need the Catalog role.": "role group name",
}

_WORDS = re.compile(r"\b(?:" + "|".join(US_TO_UK) + r")\b", re.IGNORECASE)


def _catalog():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "i18n_catalog", ROOT / "tools" / "i18n_catalog.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("tools/i18n_catalog.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("i18n_catalog", module)
    spec.loader.exec_module(module)
    return module


def american_words(text: str) -> list[str]:
    """The American words of one string, in order."""
    return [m.group(0) for m in _WORDS.finditer(text)]


def in_scope(places: list[str]) -> bool:
    return any(p.startswith(ALLOWED_APPS) for p in places)


def violations(
    found: dict[tuple[str, str | None], tuple[str | None, list[str]]],
) -> list[str]:
    """One line per msgid of an allow-listed app that uses an American word."""
    bad = []
    for (msgid, _ctx), (plural, places) in sorted(found.items(), key=lambda kv: kv[0][0]):
        if not in_scope(places) or msgid in EXCEPTIONS:
            continue
        words = american_words(msgid) + american_words(plural or "")
        if words:
            bad.append(f"{places[0]}: {sorted(set(words))} in {msgid[:80]!r}")
    return bad


def check() -> int:
    bad = violations(_catalog().collect())
    for line in bad:
        print(f"American spelling in an English msgid (I18N.23): {line}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        raise SystemExit(check())
    raise SystemExit(__doc__)
