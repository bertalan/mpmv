"""I18N.7: the Italian catalog of the Manager and account strings is complete and compiled."""

import importlib.util
import sys
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("i18n_catalog", ROOT / "tools" / "i18n_catalog.py")
assert _spec is not None
assert _spec.loader is not None
catalog = importlib.util.module_from_spec(_spec)
sys.modules["i18n_catalog"] = catalog
_spec.loader.exec_module(catalog)

PO = ROOT / "locale" / "it" / "LC_MESSAGES" / "django.po"


def test_every_scoped_msgid_is_in_the_catalog() -> None:
    have = {(e.msgid, e.msgctxt) for e in polib.pofile(str(PO))}
    assert have == set(catalog.collect()), "run `python tools/i18n_catalog.py extract`"


def test_no_scoped_msgid_is_untranslated() -> None:
    assert catalog._missing(polib.pofile(str(PO))) == []


def test_the_compiled_catalog_matches_the_source_catalog() -> None:
    assert catalog.check() == 0


def test_translations_keep_the_placeholders_of_the_source() -> None:
    import re

    pattern = re.compile(r"%\(\w+\)[sd]")
    for entry in polib.pofile(str(PO)):
        if entry.msgid_plural:
            for text in entry.msgstr_plural.values():
                assert sorted(pattern.findall(text)) == sorted(pattern.findall(entry.msgid_plural))
        else:
            assert sorted(pattern.findall(entry.msgstr)) == sorted(pattern.findall(entry.msgid))
