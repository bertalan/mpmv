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


# Mails to the vendor (formal register). `vendor_order_cancelled` and the other `vendor_*` order
# mails are named after the vendor's action and go to the customer, so they are not listed.
VENDOR_FACING_EMAILS = (
    "/vendor_package_cancelled/",
    "/vendor_amendment_applied/",
    "/vendor_order_cancelled_notice/",
    "/_invoice_notice",
)


def _vendor_facing(path: str) -> bool:
    return any(marker in path for marker in VENDOR_FACING_EMAILS)


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
        # Vendor-facing mails stay formal.
        if not any(
            p.startswith("apps/orders/templates/email/") and not _vendor_facing(p)
            for p, _l in entry.occurrences
        ):
            continue
        text = " ".join([entry.msgstr, *entry.msgstr_plural.values()])
        assert not FORMAL[lang].search(text), entry.msgid


@pytest.mark.parametrize("lang", ["de", "fr", "es"])
def test_vendor_order_emails_use_the_formal_register(lang: str) -> None:
    informal = {
        "de": re.compile(r"\b(du|dein\w*|dir|dich)\b", re.IGNORECASE),
        "fr": re.compile(r"\b(tu|ton|ta|tes|toi)\b", re.IGNORECASE),
        "es": re.compile(r"\b(tú|tu|tus|ti|contigo)\b", re.IGNORECASE),
    }[lang]
    for entry in _po(lang):
        paths = [p for p, _l in entry.occurrences if "/email/orders/" in p]
        # A string shared with a customer mail follows the customer register.
        if not paths or not all(_vendor_facing(p) for p in paths):
            continue
        text = " ".join([entry.msgstr, *entry.msgstr_plural.values()])
        assert not informal.search(text), entry.msgid


# I18N.13 follow-up: the customer privacy page (`privacy_account`) is informal, like every
# customer page; the second-factor screens (`accounts/mfa/`, shared by customers, vendors and
# staff) are formal. A string shared with another page keeps one register, so the customer page
# uses its own gettext context where it shares a msgid with a vendor page ("Your data").
PRIVACY_PAGES = ("compliance/privacy_account.html",)
MFA_PAGES = "accounts/mfa/"

INFORMAL = {
    "it": re.compile(
        r"\b(tu|tuo|tua|tuoi|tue|ti|hai|riesci|scegli|scansiona|inserisci|guarda|usa)\b",
        re.IGNORECASE,
    ),
    "de": re.compile(
        r"\b(du|dein\w*|dir|dich|gib|wähle|scanne|sieh|bewahre|verwende)\b", re.IGNORECASE
    ),
    "fr": re.compile(r"\b(tu|ton|ta|tes|toi|te)\b", re.IGNORECASE),
    "es": re.compile(
        r"\b(tú|tu|tus|ti|contigo|tienes|puedes|elige|escanea|introduce|guárdalos)\b",
        re.IGNORECASE,
    ),
}


def _texts(entry: polib.POEntry) -> str:
    return " ".join([entry.msgstr, *entry.msgstr_plural.values()])


def _only_in(entry: polib.POEntry, marker: str) -> bool:
    paths = [p for p, _l in entry.occurrences]
    return bool(paths) and all(marker in p for p in paths)


def _any_in(entry: polib.POEntry, markers: tuple[str, ...]) -> bool:
    return any(m in p for p, _l in entry.occurrences for m in markers)


@pytest.mark.parametrize("lang", ["it", "de", "fr", "es"])
def test_customer_privacy_page_uses_the_informal_register(lang: str) -> None:
    entries = [e for e in _po(lang) if _any_in(e, PRIVACY_PAGES)]
    assert {e.msgid for e in entries} >= {"Your data", "Erase your account"}
    for entry in entries:
        assert entry.msgstr, entry.msgid
        if lang in FORMAL:
            assert not FORMAL[lang].search(_texts(entry)), entry.msgid


@pytest.mark.parametrize(
    ("lang", "expected"),
    [("it", "I tuoi dati"), ("de", "Deine Daten"), ("fr", "Tes données"), ("es", "Tus datos")],
)
def test_your_data_has_a_customer_context_and_the_vendor_page_stays_formal(
    lang: str, expected: str
) -> None:
    entries = [e for e in _po(lang) if e.msgid == "Your data"]
    by_ctx = {e.msgctxt: e for e in entries}
    customer = [e for c, e in by_ctx.items() if c and _any_in(e, PRIVACY_PAGES)]
    assert len(customer) == 1
    assert customer[0].msgstr == expected
    vendor = by_ctx[None]
    assert not _any_in(vendor, PRIVACY_PAGES)
    assert _any_in(vendor, ("manager/shop/termination.html",))
    if lang != "it":
        assert vendor.msgstr != expected


@pytest.mark.parametrize("lang", ["it", "de", "fr", "es"])
def test_second_factor_pages_use_the_formal_register(lang: str) -> None:
    for entry in _po(lang):
        if not _only_in(entry, MFA_PAGES):
            continue
        assert not INFORMAL[lang].search(_texts(entry)), (entry.msgid, _texts(entry))


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("it", "Verifichi la Sua identità"),
        ("es", "Verifique su identidad"),
        ("fr", "Vérifiez votre identité"),
    ],
)
def test_the_second_factor_verify_title_is_formal(lang: str, expected: str) -> None:
    entries = {e.msgid: e.msgstr for e in _po(lang)}
    assert entries["Verify it is you"] == expected
