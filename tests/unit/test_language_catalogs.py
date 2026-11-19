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
        r"\b(tu|tuo|tua|tuoi|tue|ti|hai|riesci|scegli|scansiona|inserisci|guarda)\b",
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


# I18N.13 follow-up: the vendor application flow (VEN.22) is in the catalogs, in the formal
# register (a prospective vendor is addressed with Lei / Sie / vous / usted).
APPLY_MARKERS = (
    "apps/vendors/templates/vendors/apply/",
    "apps/vendors/templates/email/vendors/application_",
    "apps/vendors/forms.py",
)


def _apply_entries(lang: str) -> list[polib.POEntry]:
    return [e for e in _po(lang) if _any_in(e, APPLY_MARKERS)]


def test_the_vendor_application_flow_is_in_scope() -> None:
    found = catalog.collect()
    places = {p for _k, (_pl, ps) in found.items() for p in ps}
    for page in ("form", "confirm", "confirmed", "cancel", "cancelled", "invalid", "sent"):
        assert f"apps/vendors/templates/vendors/apply/{page}.html" in places, page
    for mail in ("application_confirm", "application_exists", "application_reminder"):
        assert any(f"email/vendors/{mail}/" in p for p in places), mail
    assert "apps/vendors/forms.py" in places
    assert ("Send my application", None) in found


@pytest.mark.parametrize("lang", ["it", "de", "fr", "es"])
def test_the_vendor_application_flow_is_translated_and_formal(lang: str) -> None:
    entries = _apply_entries(lang)
    assert len(entries) >= 30
    for entry in entries:
        assert _texts(entry).strip(), entry.msgid
        assert not INFORMAL[lang].search(_texts(entry)), (entry.msgid, _texts(entry))
    by_id = {e.msgid: e.msgstr for e in entries}
    assert by_id["Send my application"] != "Send my application"


# I18N.13 register follow-up: the Manager (vendor and staff screens) and the vendor mails are
# formal. The check has two parts. Informal pronouns and verb forms are refused anywhere. An
# informal imperative ("Annulla", "Scegli", "Wähle", "Choisis", "Elige") is refused at the start of
# a sentence, but not in a short label: a button or link name of at most twelve words with no
# sentence punctuation may be an infinitive or imperative (translations.md, Tone).
FORMAL_SCOPE = (
    "apps/manager/templates/",
    "apps/vendors/templates/",
    "apps/accounts/templates/accounts/mfa/",
)
SHARED_WITH_CUSTOMER = ("compliance/privacy_account.html", "apps/events/", "apps/orders/")

INFORMAL_VERBS = {
    "it": r"tu|tuo|tua|tuoi|tue|ti|hai|sei|puoi|potrai|vuoi|devi|riesci|ricordati|assicurati",
    "de": r"du|dein\w*|dir|dich|kannst|musst|willst|hast|bist|euch|euer\w*",
    "fr": r"tu|ton|ta|tes|toi|te|peux|veux|dois|as|es",
    "es": r"tú|tu|tus|ti|contigo|tienes|puedes|quieres|debes|eres",
}
IMPERATIVES = {
    "it": (
        "annulla traduci scegli inserisci clicca vai scansiona guarda seleziona compila carica usa "
        "controlla verifica aggiungi salva conferma rimuovi elimina modifica crea invia apri "
        "scrivi digita premi torna leggi ricorda attendi prova imposta indica descrivi cerca "
        "scarica collega configura registra chiedi usa trascina trova"
    ),
    "de": (
        "gib wähle scanne sieh bewahre verwende klicke gehe füge speichere lösche bestätige prüfe "
        "trage lade nutze öffne schreibe melde lege stelle achte beachte wechsle kopiere suche "
        "setze bearbeite starte aktiviere übersetze fordere gebe lies halte drücke nimm"
    ),
    "fr": (
        "ajoute choisis clique saisis vérifie enregistre supprime confirme ouvre écris scanne "
        "utilise garde conserve contrôle télécharge regarde sélectionne remplis crée envoie "
        "modifie retourne reviens annule traduis va lis appuie renseigne indique ajoute"
    ),
    "es": (
        "elige escanea introduce guárdalos haz pulsa selecciona añade guarda elimina confirma "
        "abre escribe revisa usa comprueba descarga vuelve cancela traduce crea envía ve busca "
        "rellena completa sube pide recuerda asegúrate indica configura conecta registra cambia "
        "modifica"
    ),
}
SENTENCE_START = r"(?:^|[.:;!?]\s+|\(\s*)"
INFORMAL_PRONOUN = {
    lang: re.compile(rf"\b({words})\b", re.IGNORECASE) for lang, words in INFORMAL_VERBS.items()
}
INFORMAL_IMPERATIVE = {
    lang: re.compile(SENTENCE_START + "(" + "|".join(words.split()) + r")\b", re.IGNORECASE)
    for lang, words in IMPERATIVES.items()
}


def _is_label(text: str) -> bool:
    return len(text.split()) <= 12 and not re.search(r"[.:;!?]", text)


def _formal_scope_entries(lang: str) -> list[polib.POEntry]:
    """Entries used only on formal pages (a string shared with a customer page is skipped)."""
    result = []
    for entry in _po(lang):
        paths = [p for p, _l in entry.occurrences]
        if not paths or not all(any(p.startswith(s) for s in FORMAL_SCOPE) for p in paths):
            continue
        if any(m in p for p in paths for m in SHARED_WITH_CUSTOMER):
            continue
        result.append(entry)
    return result


@pytest.mark.parametrize("lang", ["it", "de", "fr", "es"])
def test_formal_pages_have_no_informal_pronoun_or_sentence_imperative(lang: str) -> None:
    entries = _formal_scope_entries(lang)
    assert len(entries) > 100
    bad = []
    for entry in entries:
        for text in [entry.msgstr, *entry.msgstr_plural.values()]:
            if INFORMAL_PRONOUN[lang].search(text) or (
                not _is_label(text) and INFORMAL_IMPERATIVE[lang].search(text)
            ):
                bad.append((entry.msgid, text))
    assert bad == []


@pytest.mark.parametrize(
    ("lang", "text", "flagged"),
    [
        ("it", "Salva prima le modifiche: viene inviata l'ultima bozza.", True),
        ("it", "Inserisca il numero. Scegli un corriere.", True),
        ("it", "Annulla", False),
        ("it", "Il Suo ruolo non può confermare questa modifica.", False),
        ("it", "Il tuo ruolo", True),
        ("de", "Wähle eine Datei, die du hochladen willst.", True),
        ("de", "Wählen Sie eine Datei.", False),
        ("fr", "Choisis un fichier. Puis enregistre.", True),
        ("fr", "Choisissez un fichier.", False),
        ("es", "Elige un archivo. Luego guarda.", True),
        ("es", "Elija un archivo.", False),
    ],
)
def test_the_register_regexes_flag_what_they_should(lang: str, text: str, flagged: bool) -> None:
    hit = bool(INFORMAL_PRONOUN[lang].search(text)) or (
        not _is_label(text) and bool(INFORMAL_IMPERATIVE[lang].search(text))
    )
    assert hit is flagged


# I18N.13 follow-up: the Italian help uses one apostrophe, the ASCII one (the large majority
# of the help and of the catalog). A typographic one would make a quoted label differ from the
# screen's label and from a search typed on a keyboard.
def test_italian_help_uses_only_the_ascii_apostrophe() -> None:
    bad = [
        str(p.relative_to(ROOT))
        for p in sorted((ROOT / "help" / "it").rglob("*.md"))
        if re.search(r"[\u2019\u2018\u02bc]", p.read_text(encoding="utf-8"))
    ]
    assert bad == []


def test_the_italian_labels_quoted_by_the_help_use_the_ascii_apostrophe() -> None:
    labels = {
        "Use an authenticator app",
        "Set up an authenticator app",
        "Ship the order",
        "Account status",
    }
    found = {e.msgid: e.msgstr for e in _po("it") if e.msgid in labels}
    assert set(found) == labels
    assert all("\u2019" not in text for text in found.values()), found


# I18N.20 part 1: the storefront catalog (listing, search, product page includes, the
# specification labels, the fitment summary, the ride catalog overrides) is in the catalogs,
# in the informal customer register.
CATALOG_STOREFRONT = (
    "apps/catalog/templates/catalog/listing.html",
    "apps/catalog/templates/catalog/search.html",
    "apps/catalog/templates/catalog/includes/listing_filters.html",
    "apps/catalog/templates/catalog/includes/product_specs.html",
    "apps/catalog/templates/catalog/includes/product_fitment.html",
    "apps/catalog/templates/catalog/includes/fitment_group.html",
    "apps/catalog/product_pages.py",
    "apps/fitment/product_summary.py",
    "themes/ride/templates/catalog/listing.html",
    "themes/ride/templates/catalog/search.html",
    "themes/ride/templates/catalog/product.html",
    "themes/base/templates/components/gallery.html",
)


def test_the_catalog_storefront_is_in_scope() -> None:
    found = catalog.collect()
    places = {p for _k, (_pl, ps) in found.items() for p in ps}
    for path in CATALOG_STOREFRONT:
        assert path in places or not (ROOT / path).read_text().count("trans"), path
    for msgid in ("Weight", "Country of origin", "Fits everything.", "Not for", "Search the shop"):
        assert (msgid, None) in found, msgid


@pytest.mark.parametrize("lang", list(LANGS))
def test_the_catalog_storefront_entries_are_translated(lang: str) -> None:
    entries = [e for e in _po(lang) if _any_in(e, CATALOG_STOREFRONT)]
    assert len(entries) >= 30
    for entry in entries:
        assert entry.msgstr or all(entry.msgstr_plural.values()), entry.msgid


@pytest.mark.parametrize("lang", ["it", "de", "fr", "es"])
def test_the_catalog_storefront_uses_the_informal_register(lang: str) -> None:
    for entry in _po(lang):
        if not _any_in(entry, CATALOG_STOREFRONT):
            continue
        text = _texts(entry)
        if lang in FORMAL:
            assert not FORMAL[lang].search(text), entry.msgid
    checks = {e.msgid: _texts(e) for e in _po(lang)}
    expected = {
        "it": "Nessun prodotto corrisponde alla tua ricerca.",
        "de": "Keine Produkte entsprechen deiner Suche.",
        "fr": "Aucun produit ne correspond à ta recherche.",
        "es": "Ningún producto coincide con tu búsqueda.",
    }[lang]
    assert checks["No products match your search."] == expected


# I18N.20 part 2: the public event pages and feeds, the blog, the CMS blocks, the footer and the
# form page (base and ride) are in the catalogs, in the informal customer register.
PART2_STOREFRONT = (
    "apps/events/templates/events/_storefront.html",
    "apps/events/feeds.py",
    "apps/cms/templates/cms/blog_author_page.html",
    "apps/cms/templates/cms/blog_index_page.html",
    "apps/cms/templates/cms/form_page.html",
    "apps/cms/templates/cms/form_page_landing.html",
    "apps/cms/templates/cms/blocks/",
    "apps/cms/templates/cms/includes/article_meta.html",
    "apps/cms/forms_pages.py",
    "themes/ride/templates/cms/",
    "themes/ride/templates/components/",
    "themes/base/templates/components/",
)


def test_the_part_2_storefront_is_in_scope() -> None:
    found = catalog.collect()
    places = {p for _k, (_pl, ps) in found.items() for p in ps}
    for path in PART2_STOREFRONT:
        assert any(p.startswith(path) for p in places), path
    for msgid in (
        "All events of %(name)s",
        "Events of %(vendor)s",
        "No articles by this author yet.",
        "Send",
        "Thank you. We have received your message.",
        "Time left:",
        "Available now",
        "Too many submissions, try again shortly.",
    ):
        assert (msgid, None) in found, msgid


@pytest.mark.parametrize("lang", list(LANGS))
def test_the_part_2_storefront_entries_are_translated(lang: str) -> None:
    entries = [e for e in _po(lang) if _any_in(e, PART2_STOREFRONT)]
    assert len(entries) >= 60
    for entry in entries:
        assert entry.msgstr or all(entry.msgstr_plural.values()), entry.msgid


@pytest.mark.parametrize("lang", ["de", "fr", "es"])
def test_the_part_2_storefront_uses_the_informal_register(lang: str) -> None:
    for entry in _po(lang):
        # The form page module also holds the editor's help texts, which keep the formal register.
        if _any_in(entry, PART2_STOREFRONT) and not _only_in(entry, "apps/cms/forms_pages.py"):
            assert not FORMAL[lang].search(_texts(entry)), entry.msgid


@pytest.mark.parametrize("lang", list(LANGS))
def test_the_part_2_labels_differ_from_the_english_source(lang: str) -> None:
    checks = {e.msgid: _texts(e) for e in _po(lang)}
    for msgid in ("Send", "Time left:", "No articles by this author yet.", "Pause countdown"):
        assert checks[msgid] != msgid, msgid


# I18N.20 part 3: the template scope guard. Every template that carries translation tags is in the
# catalog scope (`tools/i18n_catalog.py` SCOPES) or in EXCLUDED below, with a reason. A new template
# with `{% translate %}` therefore fails here until somebody decides where it belongs.
# The entries below are the storefront and staff templates still English-only; each one is
# tracked by the named follow-up slice and leaves this set when it joins SCOPES.
EXCLUDED = {
    # Editor, admin and Wagtail screens: formal register, own slice.
    "apps/catalog/templates/admin/": "admin-only, I18N.20 part 3b",
    "apps/catalog/templates/catalog/admin/": "admin-only, I18N.20 part 3b",
    "apps/cms/templates/cms/admin/": "editor-only, I18N.20 part 3b",
    "apps/cms/templates/wagtail_localize/": "editor-only, I18N.20 part 3b",
    "apps/core/templates/admin/": "admin-only, I18N.20 part 3b",
    "apps/events/templates/events/admin/": "admin-only, I18N.20 part 3b",
    "apps/vendors/templates/admin/": "admin-only, I18N.20 part 3b",
    "themes/ride/templates/manager/": "Manager override, I18N.20 part 3b",
    "themes/ride/templates/layouts/": "layout override, I18N.20 part 3b",
    "themes/base/templates/styleguide.html": "developer style guide, not shown to customers",
    "themes/ride/templates/styleguide": "developer style guide, not shown to customers",
    # Email templates: their own scope (one language per recipient), I18N.20 part 3c.
    "apps/catalog/templates/email/": "email templates, own scope, I18N.20 part 3c",
    "apps/compliance/templates/email/": "email templates, own scope, I18N.20 part 3c",
    "apps/core/templates/email/": "email templates, own scope, I18N.20 part 3c",
    "apps/vendors/templates/email/": "email templates, own scope, I18N.20 part 3c",
    # Customer-facing pages not yet translated: informal register, I18N.20 part 3b.
    "apps/cms/templates/cms/": "legal and landing pages, I18N.20 part 3b",
    "apps/core/templates/core/components/": "image credits, I18N.20 part 3b",
    "apps/fitment/templates/fitment/": "garage saver, I18N.20 part 3b",
    "themes/ride/templates/accounts/": "ride account overrides, I18N.20 part 3b",
    "themes/ride/templates/orders/": "ride order overrides, I18N.20 part 3b",
    "themes/ride/templates/seo/": "ride consent banner, I18N.20 part 3b",
}
_TRANSLATION_TAG = re.compile(r"\{%-?\s*(?:translate|trans|blocktranslate|blocktrans)\b")


def _templates_with_translation_tags() -> list[str]:
    found = []
    for pattern in ("apps/*/templates", "themes/*/templates"):
        for base in sorted(ROOT.glob(pattern)):
            for path in sorted(base.rglob("*.*")):
                if path.suffix not in {".html", ".txt"}:
                    continue
                if _TRANSLATION_TAG.search(path.read_text(encoding="utf-8")):
                    found.append(str(path.relative_to(ROOT)))
    return found


def test_every_translatable_template_is_in_scope_or_excluded() -> None:
    covered = {str(p.relative_to(ROOT)) for p in catalog._files()}
    offenders = [
        t
        for t in _templates_with_translation_tags()
        if t not in covered and not t.startswith(tuple(EXCLUDED))
    ]
    assert offenders == [], f"add to SCOPES or to EXCLUDED with a reason: {offenders}"


def test_the_exclusions_have_reasons_and_are_not_stale() -> None:
    assert all(EXCLUDED.values())
    for prefix in EXCLUDED:
        assert any(t.startswith(prefix) for t in _templates_with_translation_tags()), prefix


# Part 2 minors: the form page module mixes the editor's labels and help texts (formal) with two
# public refusals (customer text, informal). Each public string is pinned by msgid, so a new one
# in that module has to be placed on purpose.
FORM_PAGE_PUBLIC = ("Too many submissions, try again shortly.", "This submission is too large.")
FORM_PAGE_EDITOR_LABELS = (
    "introduction",
    "thank-you text",
    "button label",
    "Form fields",
    "form page",
    "keep submissions for (days)",
)


@pytest.mark.parametrize("lang", ["de", "fr", "es"])
def test_the_form_page_module_has_a_register_per_string(lang: str) -> None:
    entries = {e.msgid: e for e in _po(lang) if _only_in(e, "apps/cms/forms_pages.py")}
    for msgid in FORM_PAGE_PUBLIC:
        assert not FORMAL[lang].search(_texts(entries[msgid])), msgid
    for msgid in entries:
        if msgid not in FORM_PAGE_PUBLIC:
            assert msgid in FORM_PAGE_EDITOR_LABELS or len(msgid) > 40, msgid  # help texts: formal


def test_the_spanish_apply_now_is_informal_and_the_german_author_line_is_short() -> None:
    es = {e.msgid: e.msgstr for e in _po("es")}
    de = {e.msgid: e.msgstr for e in _po("de")}
    assert es["Apply now"] == "Solicita ahora"
    assert de["No articles by this author yet."] == "Von dieser Person gibt es noch keine Artikel."


# I18N.20 part 3b, slice 1: the consumer-law storefront pages (consumer information, withdrawal,
# privacy requests, notices, product safety, legal pages, the ride legal recap) are translated
# in the informal customer register. Legal wording is an AI draft behind I18N.19 and needs
# human review.
CONSUMER_LAW_MARKERS = (
    "apps/compliance/templates/compliance/",
    "themes/ride/templates/compliance/",
    "apps/cms/templates/cms/legal_page",
    "themes/ride/templates/orders/_legal_",
)


@pytest.mark.parametrize("lang", ["it", "de", "fr", "es"])
def test_consumer_law_pages_are_translated_in_the_informal_register(lang: str) -> None:
    entries = [e for e in _po(lang) if _any_in(e, CONSUMER_LAW_MARKERS)]
    ids = {e.msgid for e in entries}
    assert {"Right of withdrawal", "Withdrawal received", "Product safety"} <= ids
    for entry in entries:
        assert _texts(entry).strip(), entry.msgid
        if lang in FORMAL:
            assert not FORMAL[lang].search(_texts(entry)), entry.msgid


@pytest.mark.parametrize("lang", ["de", "fr", "es"])
def test_the_withdrawal_terms_of_art_follow_each_country(lang: str) -> None:
    terms = {"de": "widerruf", "fr": "rétractation", "es": "desistimiento"}
    checks = {e.msgid: _texts(e).lower() for e in _po(lang)}
    assert terms[lang] in checks["Right of withdrawal"].lower()
    assert terms[lang] in checks["Withdrawal received"].lower()
