"""I18N.7 slice 2: German, French and Spanish cover the same scope as Italian, fully translated."""

import importlib.util
import re
import sys
from pathlib import Path

import polib
import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("i18n_catalog", ROOT / "tools" / "i18n_catalog.py")
assert _spec is not None
assert _spec.loader is not None
catalog = importlib.util.module_from_spec(_spec)
sys.modules["i18n_catalog"] = catalog
_spec.loader.exec_module(catalog)

LANGS = ("it", "de", "fr", "es")
TOKEN = re.compile(r"%\([^)]+\)[sdif]|\{[^}]*\}|%[sd]")


def _po(lang: str) -> polib.POFile:
    return polib.pofile(str(ROOT / "locale" / lang / "LC_MESSAGES" / "django.po"))


@pytest.mark.parametrize("lang", list(LANGS))
def test_every_language_has_the_full_scope(lang: str) -> None:
    assert {(e.msgid, e.msgctxt) for e in _po(lang)} == set(catalog.collect())


@pytest.mark.parametrize("lang", list(LANGS))
def test_no_entry_is_untranslated(lang: str) -> None:
    assert catalog._missing(_po(lang)) == []


@pytest.mark.parametrize("lang", ["de", "fr", "es"])
def test_translations_keep_placeholders_and_edge_whitespace(lang: str) -> None:
    for entry in _po(lang):
        pairs = [(entry.msgid, entry.msgstr)]
        if entry.msgid_plural:
            pairs = [
                (entry.msgid, entry.msgstr_plural[0]),
                (entry.msgid_plural, entry.msgstr_plural[1]),
            ]
        for source, text in pairs:
            assert sorted(TOKEN.findall(text)) == sorted(TOKEN.findall(source)), source
            assert source[:1].isspace() == text[:1].isspace(), source
            assert source[-1:].isspace() == text[-1:].isspace(), source


def test_the_language_switch_notice_is_translated() -> None:
    notice = "This content is not available in this language."
    for lang in ("de", "fr", "es"):
        entries = {e.msgid: e.msgstr for e in _po(lang)}
        if notice in entries:
            assert entries[notice]
            assert entries[notice] != notice


# I18N.13: customer emails address the customer informally (du / tu / tu); staff and vendor
# strings stay formal. The formal forms below must not appear in the order email entries.
FORMAL = {
    "de": re.compile(r"\b(Sie|Ihr\w*|Ihnen)\b"),
    "fr": re.compile(r"\b(vous|votre|vos|Vous|Votre|Vos)\b"),
    "es": re.compile(r"\b([Uu]sted(es)?)\b"),
}


@pytest.mark.parametrize("lang", ["de", "fr", "es"])
def test_customer_order_emails_use_the_informal_register(lang: str) -> None:
    for entry in _po(lang):
        if not any(p.startswith("apps/orders/templates/email/") for p, _l in entry.occurrences):
            continue
        text = " ".join([entry.msgstr, *entry.msgstr_plural.values()])
        assert not FORMAL[lang].search(text), entry.msgid
