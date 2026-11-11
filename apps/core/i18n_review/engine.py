"""Rules and wording of the translation review files (I18N.13): pure functions, no Django.

The rule texts below are shown in the "Read me" sheet and quoted again when a row is refused on
import, so the reviewer sees the same words both times. English is the fallback; a reader
language needs an entry in `RULES` and `READ_ME` (today: English and Italian).
"""

import hashlib
import re
from collections import Counter
from enum import StrEnum


class Rule(StrEnum):
    PLACEHOLDERS = "placeholders"
    HTML_TAGS = "html_tags"
    NEW_URL = "new_url"
    LENGTH = "length"
    CHECKSUM = "checksum"
    STALE = "stale"


RULES: dict[str, dict[Rule, str]] = {
    "en": {
        Rule.PLACEHOLDERS: (
            "Keep every placeholder exactly as it is, such as %(name)s, {name} and %s: do not "
            "translate, add, remove or rename them."
        ),
        Rule.HTML_TAGS: (
            "Keep every HTML tag exactly as it is, such as <b>, </b> and <a href=...>: do not "
            "add, remove or change tags or their attributes."
        ),
        Rule.NEW_URL: (
            "Do not add links: a web address (http://, https://, www.) that is not in the "
            "English source is refused."
        ),
        Rule.LENGTH: "Respect the limit in the Max length column: a longer text is refused.",
        Rule.CHECKSUM: (
            "Do not touch the msgid, context, form, English source, Current translation or "
            "Checksum columns: a row changed in any of them is refused."
        ),
        Rule.STALE: (
            "The text was changed in the system after this file was exported: ask for a fresh "
            "file and edit that one."
        ),
    },
    "it": {
        Rule.PLACEHOLDERS: (
            "Lascia ogni segnaposto esattamente com'è, come %(name)s, {name} e %s: non tradurli, "
            "non aggiungerli, non toglierli e non rinominarli."
        ),
        Rule.HTML_TAGS: (
            "Lascia ogni tag HTML esattamente com'è, come <b>, </b> e <a href=...>: non "
            "aggiungere, togliere o cambiare tag o i loro attributi."
        ),
        Rule.NEW_URL: (
            "Non aggiungere link: un indirizzo web (http://, https://, www.) che non è nel testo "
            "inglese viene rifiutato."
        ),
        Rule.LENGTH: (
            "Rispetta il limite della colonna Max length: un testo più lungo viene rifiutato."
        ),
        Rule.CHECKSUM: (
            "Non toccare le colonne msgid, context, form, English source, Current translation e "
            "Checksum: una riga modificata in una di esse viene rifiutata."
        ),
        Rule.STALE: (
            "Il testo è stato cambiato nel sistema dopo l'esportazione di questo file: chiedi un "
            "file nuovo e modifica quello."
        ),
    },
}

# Informal address in customer emails (owner decision).
TONE = {"it": "tu", "de": "du", "fr": "tu", "es": "tú"}

READ_ME: dict[str, dict[str, str]] = {
    "en": {
        "title": "Read me first: translation review for language {lang}",
        "intro": (
            "Thank you for reviewing these texts. Each row is one text shown to customers or "
            "vendors. Read the English source and the current translation, and write a better "
            "version only where the current one is wrong or sounds unnatural."
        ),
        "edit": (
            "Edit only the 'Reviewer translation' column (and write doubts in 'Notes'). Leave it "
            "empty to keep the current translation. Do not touch any other column; they are "
            "locked."
        ),
        "tone": (
            "Tone: customer emails use the informal form of address ('{form}'). Keep the same "
            "tone in every text."
        ),
        "doubts": (
            "Doubts: write them in the 'Notes' column. Never change the English source, and do "
            "not change a text just because you would put it differently."
        ),
        "rules": "Rules checked when the file is imported:",
        "send": (
            "When you are done, save the file and send it back to the person who sent it to you, "
            "by email. Do not rename the sheets or add rows or columns."
        ),
        "columns": (
            "Flags: 'fuzzy' means a machine or unconfirmed translation, 'missing' means there is "
            "no translation yet."
        ),
    },
    "it": {
        "title": "Leggi prima: revisione delle traduzioni per la lingua {lang}",
        "intro": (
            "Grazie per la revisione. Ogni riga è un testo mostrato a clienti o venditori. Leggi "
            "il testo inglese e la traduzione attuale, e scrivi una versione migliore solo dove "
            "quella attuale è sbagliata o suona poco naturale."
        ),
        "edit": (
            "Modifica solo la colonna 'Reviewer translation' (e scrivi i dubbi in 'Notes'). "
            "Lasciala vuota per tenere la traduzione attuale. Non toccare le altre colonne: sono "
            "bloccate."
        ),
        "tone": (
            "Tono: le email ai clienti usano la forma informale ('{form}'). Mantieni lo stesso "
            "tono in ogni testo."
        ),
        "doubts": (
            "Dubbi: scrivili nella colonna 'Notes'. Non cambiare mai il testo inglese e non "
            "cambiare un testo solo perché lo diresti in un altro modo."
        ),
        "rules": "Regole controllate quando il file viene importato:",
        "send": (
            "Quando hai finito, salva il file e rimandalo per email a chi te lo ha inviato. Non "
            "rinominare i fogli e non aggiungere righe o colonne."
        ),
        "columns": (
            "Flags: 'fuzzy' indica una traduzione automatica o non confermata, 'missing' indica "
            "che non c'è ancora una traduzione."
        ),
    },
}

BANNERS = {
    "en": (
        "Edit only 'Reviewer translation' (doubts in 'Notes'). Keep %(name)s, {name}, %s and "
        "HTML tags exactly. No new links. Respect 'Max length'. Informal tone ('{form}'). "
        "Full rules: sheet 'Read me'."
    ),
    "it": (
        "Modifica solo 'Reviewer translation' (dubbi in 'Notes'). Lascia identici %(name)s, "
        "{name}, %s e i tag HTML. Nessun link nuovo. Rispetta 'Max length'. Tono informale "
        "('{form}'). Regole complete: foglio 'Read me'."
    ),
}


def read_me_language(lang: str) -> str:
    """The language of the Read me for a reviewer of `lang`: theirs when it exists, else English."""
    return lang if lang in READ_ME else "en"


def rule_text(rule: Rule, reader: str) -> str:
    return RULES.get(reader, RULES["en"])[rule]


def read_me_lines(reader: str, lang: str) -> list[str]:
    text = READ_ME.get(reader, READ_ME["en"])
    form = TONE.get(lang, "")
    lines = [
        text["title"].format(lang=lang.upper()),
        text["intro"],
        text["edit"],
        text["columns"],
        text["tone"].format(form=form),
        text["doubts"],
        text["rules"],
        *(rule_text(rule, reader) for rule in Rule),
        text["send"],
    ]
    return lines


def banner(reader: str, lang: str) -> str:
    return BANNERS.get(reader, BANNERS["en"]).replace("{form}", TONE.get(lang, ""))


def row_message(row: int, rule: Rule, reader: str) -> str:
    label = "riga" if reader == "it" else "row"
    return f"{label} {row}: {rule_text(rule, reader)}"


_PLACEHOLDER = re.compile(r"%\([A-Za-z_][A-Za-z0-9_]*\)[sdifrc]|%[sdifrc]|\{[A-Za-z0-9_]*\}")
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_URL = re.compile(r"""(?:https?://|www\.)[^\s<>"']+""", re.IGNORECASE)


def placeholders(text: str) -> Counter[str]:
    return Counter(_PLACEHOLDER.findall(text))


def html_tags(text: str) -> Counter[str]:
    return Counter(_TAG.findall(text))


def urls(text: str) -> set[str]:
    return set(_URL.findall(text))


def max_length(msgid: str) -> int:
    """Longest accepted translation: twice the English text, at least 60 characters."""
    return max(60, 2 * len(msgid))


def validate(source: str, _current: str, reviewer: str, limit: int) -> list[Rule]:
    """The rules a reviewer's text breaks, in a stable order (empty when it is fine)."""
    broken: list[Rule] = []
    if placeholders(source) != placeholders(reviewer):
        broken.append(Rule.PLACEHOLDERS)
    if html_tags(source) != html_tags(reviewer):
        broken.append(Rule.HTML_TAGS)
    if urls(reviewer) - urls(source):
        broken.append(Rule.NEW_URL)
    if len(reviewer) > limit:
        broken.append(Rule.LENGTH)
    return broken


def checksum(ctx: str, msgid: str, form: int | None, english: str, current: str) -> str:
    form_text = "" if form is None else str(form)
    payload = "\x1f".join((ctx, msgid, form_text, english, current))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
