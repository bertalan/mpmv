"""Rules and wording of the translation review files (I18N.13): pure functions, no Django.

The rule texts below are shown in the "Read me" sheet and quoted again when a row is refused on
import, so the reviewer sees the same words both times. English is the fallback; a reader
language needs an entry in `RULES` and `READ_ME` (today: English and Italian).
"""

import hashlib
import html
import re
from collections import Counter
from enum import StrEnum
from html.parser import HTMLParser


class Rule(StrEnum):
    PLACEHOLDERS = "placeholders"
    HTML_TAGS = "html_tags"
    NEW_URL = "new_url"
    LENGTH = "length"
    CHECKSUM = "checksum"
    STALE = "stale"
    FORMULA = "formula"
    DUPLICATE = "duplicate"


RULES: dict[str, dict[Rule, str]] = {
    "en": {
        Rule.PLACEHOLDERS: (
            "Keep every placeholder exactly as it is, such as %(name)s, %d, %.2f, {name}, {0}, "
            "{{ name }} and {% tag %}: do not "
            "translate, add, remove or rename them."
        ),
        Rule.HTML_TAGS: (
            "Keep every HTML tag exactly as it is, such as <b>, </b> and <a href=...>: do not "
            "add, remove or change tags or their attributes."
        ),
        Rule.NEW_URL: (
            "Do not add links: a web address (http:, https:, ftp:, mailto:, javascript:, data:, "
            "//host, www.) that is not in the "
            "English source is refused."
        ),
        Rule.LENGTH: (
            "Max length is a guide, not a limit: a longer text is accepted but reported as a "
            "warning, so keep it short where the space is tight."
        ),
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
            "Lascia ogni segnaposto esattamente com'è, come %(name)s, %d, %.2f, {name}, {0}, {{ "
            "name }} e {% tag %}: non tradurli, "
            "non aggiungerli, non toglierli e non rinominarli."
        ),
        Rule.HTML_TAGS: (
            "Lascia ogni tag HTML esattamente com'è, come <b>, </b> e <a href=...>: non "
            "aggiungere, togliere o cambiare tag o i loro attributi."
        ),
        Rule.NEW_URL: (
            "Non aggiungere link: un indirizzo web (http:, https:, ftp:, mailto:, javascript:, "
            "data:, //host, www.) che non è nel testo "
            "inglese viene rifiutato."
        ),
        Rule.LENGTH: (
            "Max length è un valore guida, non un limite: un testo più lungo viene accettato ma "
            "segnalato come avviso, quindi tienilo breve dove lo spazio è poco."
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


RULES["de"] = {
    Rule.PLACEHOLDERS: (
        "Lass jeden Platzhalter genau so, wie er ist, zum Beispiel %(name)s, %d, %.2f, {name}, "
        "{0}, {{ name }} und {% tag %}: "
        "nicht übersetzen, nicht hinzufügen, nicht entfernen und nicht umbenennen."
    ),
    Rule.HTML_TAGS: (
        "Lass jeden HTML-Tag genau so, wie er ist, zum Beispiel <b>, </b> und <a href=...>: "
        "keine Tags oder Attribute hinzufügen, entfernen oder ändern."
    ),
    Rule.NEW_URL: (
        "Füge keine Links hinzu: Eine Webadresse (http:, https:, ftp:, mailto:, javascript:, "
        "data:, //host, www.), die nicht im "
        "englischen Text steht, wird abgelehnt."
    ),
    Rule.LENGTH: (
        "Max length ist ein Richtwert, keine Grenze: Ein längerer Text wird angenommen, aber als "
        "Warnung gemeldet. Halte ihn kurz, wo wenig Platz ist."
    ),
    Rule.CHECKSUM: (
        "Ändere nichts in den Spalten msgid, context, form, English source, Current translation "
        "und Checksum: Eine Zeile, die dort geändert wurde, wird abgelehnt."
    ),
    Rule.STALE: (
        "Der Text wurde im System geändert, nachdem diese Datei exportiert wurde: Frag nach einer "
        "neuen Datei und bearbeite diese."
    ),
}
RULES["fr"] = {
    Rule.PLACEHOLDERS: (
        "Garde chaque variable exactement telle quelle, par exemple %(name)s, %d, %.2f, {name}, "
        "{0}, {{ name }} et {% tag %} : ne "
        "les traduis pas, ne les ajoute pas, ne les supprime pas et ne les renomme pas."
    ),
    Rule.HTML_TAGS: (
        "Garde chaque balise HTML exactement telle quelle, par exemple <b>, </b> et <a href=...> : "
        "n'ajoute, ne supprime et ne modifie ni balise ni attribut."
    ),
    Rule.NEW_URL: (
        "N'ajoute pas de lien : une adresse web (http:, https:, ftp:, mailto:, javascript:, "
        "data:, //host, www.) qui n'est pas dans le "
        "texte anglais est refusée."
    ),
    Rule.LENGTH: (
        "Max length est une valeur indicative, pas une limite : un texte plus long est accepté "
        "mais signalé par un avertissement. Reste bref là où la place manque."
    ),
    Rule.CHECKSUM: (
        "Ne touche pas aux colonnes msgid, context, form, English source, Current translation et "
        "Checksum : une ligne modifiée dans l'une d'elles est refusée."
    ),
    Rule.STALE: (
        "Le texte a été modifié dans le système après l'export de ce fichier : demande un "
        "nouveau fichier et modifie celui-là."
    ),
}
RULES["es"] = {
    Rule.PLACEHOLDERS: (
        "Deja cada marcador exactamente como está, por ejemplo %(name)s, %d, %.2f, {name}, {0}, "
        "{{ name }} y {% tag %}: no los "
        "traduzcas, no los añadas, no los quites y no los cambies de nombre."
    ),
    Rule.HTML_TAGS: (
        "Deja cada etiqueta HTML exactamente como está, por ejemplo <b>, </b> y <a href=...>: no "
        "añadas, quites ni cambies etiquetas ni sus atributos."
    ),
    Rule.NEW_URL: (
        "No añadas enlaces: una dirección web (http:, https:, ftp:, mailto:, javascript:, data:, "
        "//host, www.) que no esté en el texto "
        "inglés se rechaza."
    ),
    Rule.LENGTH: (
        "Max length es una guía, no un límite: un texto más largo se acepta pero se avisa con una "
        "advertencia. Sé breve donde falta espacio."
    ),
    Rule.CHECKSUM: (
        "No toques las columnas msgid, context, form, English source, Current translation y "
        "Checksum: una fila cambiada en cualquiera de ellas se rechaza."
    ),
    Rule.STALE: (
        "El texto se cambió en el sistema después de exportar este archivo: pide un archivo nuevo "
        "y edita ese."
    ),
}

RULES["en"][Rule.FORMULA] = (
    "Write plain text only: a cell in Reviewer translation or Notes that starts with = (a "
    "formula) is refused."
)
RULES["en"][Rule.DUPLICATE] = (
    "Do not copy or add rows: a text that appears twice in the file is refused."
)
RULES["it"][Rule.FORMULA] = (
    "Scrivi solo testo semplice: una cella di Reviewer translation o Notes che inizia con = "
    "(una formula) viene rifiutata."
)
RULES["it"][Rule.DUPLICATE] = (
    "Non copiare né aggiungere righe: un testo che compare due volte nel file viene rifiutato."
)
RULES["de"][Rule.FORMULA] = (
    "Schreib nur einfachen Text: Eine Zelle in Reviewer translation oder Notes, die mit = "
    "beginnt (eine Formel), wird abgelehnt."
)
RULES["de"][Rule.DUPLICATE] = (
    "Kopiere oder ergänze keine Zeilen: Ein Text, der zweimal in der Datei steht, wird abgelehnt."
)
RULES["fr"][Rule.FORMULA] = (
    "Écris du texte simple uniquement : une cellule de Reviewer translation ou Notes qui "
    "commence par = (une formule) est refusée."
)
RULES["fr"][Rule.DUPLICATE] = (
    "Ne copie et n'ajoute pas de lignes : un texte qui apparaît deux fois dans le fichier est "
    "refusé."
)
RULES["es"][Rule.FORMULA] = (
    "Escribe solo texto sin formato: una celda de Reviewer translation o Notes que empiece por = "
    "(una fórmula) se rechaza."
)
RULES["es"][Rule.DUPLICATE] = (
    "No copies ni añadas filas: un texto que aparece dos veces en el archivo se rechaza."
)

READ_ME["de"] = {
    "title": "Zuerst lesen: Übersetzungsprüfung für die Sprache {lang}",
    "intro": (
        "Danke für die Prüfung. Jede Zeile ist ein Text, den Kunden oder Verkäufer sehen. Lies den "
        "englischen Text und die aktuelle Übersetzung und schreib nur dann eine bessere Fassung, "
        "wenn die aktuelle falsch ist oder unnatürlich klingt."
    ),
    "edit": (
        "Bearbeite nur die Spalte 'Reviewer translation' (und schreib Zweifel in 'Notes'). Lass "
        "sie leer, wenn die aktuelle Übersetzung bleiben soll. Fass keine andere Spalte an: Sie "
        "sind gesperrt."
    ),
    "tone": (
        "Ton: E-Mails an Kunden verwenden die vertraute Anrede ('{form}'). Halte in jedem Text "
        "denselben Ton."
    ),
    "doubts": (
        "Zweifel: Schreib sie in die Spalte 'Notes'. Ändere nie den englischen Text und ändere "
        "einen Text nicht nur, weil du ihn anders sagen würdest."
    ),
    "rules": "Diese Regeln werden beim Import geprüft:",
    "send": (
        "Wenn du fertig bist, speichere die Datei und schick sie per E-Mail an die Person "
        "zurück, die sie dir geschickt hat. Benenne die Blätter nicht um und füge keine Zeilen "
        "oder Spalten hinzu."
    ),
    "columns": (
        "Flags: 'fuzzy' ist eine maschinelle oder unbestätigte Übersetzung, 'missing' heißt, "
        "dass es noch keine Übersetzung gibt."
    ),
}
READ_ME["fr"] = {
    "title": "À lire d'abord : relecture des traductions pour la langue {lang}",
    "intro": (
        "Merci de relire ces textes. Chaque ligne est un texte que voient les clients ou les "
        "vendeurs. Lis le texte anglais et la traduction actuelle, et écris une meilleure version "
        "seulement si l'actuelle est fausse ou sonne mal."
    ),
    "edit": (
        "Modifie uniquement la colonne 'Reviewer translation' (et écris tes doutes dans 'Notes'). "
        "Laisse-la vide pour garder la traduction actuelle. Ne touche à aucune autre colonne : "
        "elles sont verrouillées."
    ),
    "tone": (
        "Ton : les e-mails aux clients utilisent la forme familière ('{form}'). Garde le même "
        "ton dans tous les textes."
    ),
    "doubts": (
        "Doutes : écris-les dans la colonne 'Notes'. Ne change jamais le texte anglais et ne "
        "change pas un texte seulement parce que tu le dirais autrement."
    ),
    "rules": "Règles vérifiées à l'import du fichier :",
    "send": (
        "Quand tu as fini, enregistre le fichier et renvoie-le par e-mail à la personne qui te "
        "l'a envoyé. Ne renomme pas les feuilles et n'ajoute ni ligne ni colonne."
    ),
    "columns": (
        "Flags : 'fuzzy' signale une traduction automatique ou non confirmée, 'missing' signale "
        "qu'il n'y a pas encore de traduction."
    ),
}
READ_ME["es"] = {
    "title": "Lee esto primero: revisión de traducciones para el idioma {lang}",
    "intro": (
        "Gracias por revisar estos textos. Cada fila es un texto que ven los clientes o los "
        "vendedores. Lee el texto en inglés y la traducción actual, y escribe una versión mejor "
        "solo si la actual es incorrecta o suena poco natural."
    ),
    "edit": (
        "Edita solo la columna 'Reviewer translation' (y escribe tus dudas en 'Notes'). Déjala "
        "vacía para mantener la traducción actual. No toques ninguna otra columna: están "
        "bloqueadas."
    ),
    "tone": (
        "Tono: los correos a los clientes usan el trato informal ('{form}'). Mantén el mismo tono "
        "en todos los textos."
    ),
    "doubts": (
        "Dudas: escríbelas en la columna 'Notes'. No cambies nunca el texto en inglés y no "
        "cambies un texto solo porque tú lo dirías de otra forma."
    ),
    "rules": "Reglas que se comprueban al importar el archivo:",
    "send": (
        "Cuando termines, guarda el archivo y devuélvelo por correo a la persona que te lo envió. "
        "No cambies el nombre de las hojas ni añadas filas o columnas."
    ),
    "columns": (
        "Flags: 'fuzzy' indica una traducción automática o sin confirmar, 'missing' indica que "
        "todavía no hay traducción."
    ),
}

BANNERS["de"] = (
    "Bearbeite nur 'Reviewer translation' (Zweifel in 'Notes'). %(name)s, {name}, %s und "
    "HTML-Tags unverändert lassen. Keine neuen Links. 'Max length' ist ein Richtwert. Vertraute "
    "Anrede ('{form}'). Alle Regeln: Blatt 'Read me'."
)
BANNERS["fr"] = (
    "Modifie seulement 'Reviewer translation' (doutes dans 'Notes'). Garde %(name)s, {name}, %s "
    "et les balises HTML tels quels. Aucun nouveau lien. 'Max length' est indicatif. Ton "
    "familier ('{form}'). Toutes les règles : feuille 'Read me'."
)
BANNERS["es"] = (
    "Edita solo 'Reviewer translation' (dudas en 'Notes'). Deja %(name)s, {name}, %s y las "
    "etiquetas HTML tal cual. Sin enlaces nuevos. 'Max length' es una guía. Trato informal "
    "('{form}'). Todas las reglas: hoja 'Read me'."
)


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
    label = {"it": "riga", "de": "Zeile", "fr": "ligne", "es": "fila"}.get(reader, "row")
    return f"{label} {row}: {rule_text(rule, reader)}"


_TEMPLATE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
_PRINTF = re.compile(
    r"%(?:\([^)]*\))?[#0\-+]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa%]"
)
_FIELD = re.compile(r"\{[^{}]*\}")
_REST = r"""[^\s"'<>]*"""
_LINKS = (
    re.compile(r"(?:https?|ftp|mailto|javascript|data):" + _REST, re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9:/])//(?=[A-Za-z0-9\[])" + _REST),
    re.compile(r"\bwww\." + _REST, re.IGNORECASE),
)
_HIDDEN = re.compile(r"[\x00-\x1f\u200b-\u200f\u2028\u2029\ufeff]")


def placeholders(text: str) -> Counter[str]:
    """Every placeholder of `text`: Django template syntax, printf and str.format fields.

    Each one is compared whole (flags, width, precision, conversion, format spec, attribute or
    index access included), so a translation can reorder them but never change or add one."""
    found: Counter[str] = Counter()

    def take(pattern: re.Pattern[str], kind: str, value: str) -> str:
        for m in pattern.finditer(value):
            found[f"{kind}:{m.group(0)}"] += 1
        return pattern.sub(" ", value)

    rest = take(_TEMPLATE, "tpl", text)
    rest = take(_PRINTF, "printf", rest)
    take(_FIELD, "field", rest)
    return found


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: Counter[str] = Counter()
        self.events = False

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], kind: str) -> None:
        names = sorted((n.lower(), v or "") for n, v in attrs)
        if any(n.startswith("on") for n, _ in names):
            self.events = True
        self.found[f"{kind}:{tag.lower()}:{names!r}"] += 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, "start")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, "empty")

    def handle_endtag(self, tag: str) -> None:
        self.found[f"end:{tag.lower()}"] += 1


def html_tags(text: str) -> Counter[str]:
    """Tags and attributes of `text` read with a real HTML parser; a `<` that does not open a
    parsed tag (a broken or unclosed one) is counted too."""
    parser = _Tags()
    parser.feed(text)
    parser.close()
    found = parser.found
    found["event-handler"] += 1 if parser.events else 0
    found["lt"] = text.count("<")
    return found


def urls(text: str) -> set[str]:
    """Link-like tokens: schemes (also hidden by entities, case or control characters),
    protocol-relative `//host` and `www.`."""
    plain = html.unescape(text)
    compact = _HIDDEN.sub("", plain)
    tokens: set[str] = set()
    for value in (plain, compact):
        for pattern in _LINKS:
            tokens.update(m.group(0).lower() for m in pattern.finditer(value))
    return tokens


def max_length(msgid: str) -> int:
    """Longest accepted translation: twice the English text, at least 60 characters."""
    return max(60, 2 * len(msgid))


def validate(source: str, _current: str, reviewer: str, _limit: int) -> list[Rule]:
    """The rules that refuse a reviewer's text, in a stable order (empty when it is fine)."""
    broken: list[Rule] = []
    if placeholders(source) != placeholders(reviewer):
        broken.append(Rule.PLACEHOLDERS)
    theirs = html_tags(reviewer)
    if html_tags(source) != theirs or theirs["event-handler"]:
        broken.append(Rule.HTML_TAGS)
    if urls(reviewer) - urls(source):
        broken.append(Rule.NEW_URL)
    return broken


def over_guide(reviewer: str, limit: int) -> bool:
    """True when a text is longer than the Max length guide (a warning, never a refusal)."""
    return len(reviewer) > limit


def checksum(ctx: str, msgid: str, form: int | None, english: str, current: str) -> str:
    form_text = "" if form is None else str(form)
    payload = "\x1f".join((ctx, msgid, form_text, english, current))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
