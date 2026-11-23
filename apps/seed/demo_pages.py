"""THM.23 2/3: the richer About page, the Contact page and the platform contact points.

Texts are invented. English uses British spelling and German standard German (no regional
variant); a language without its own text shows the source text. The About page itself is made
by CMS.1 (`seed_site`); the seed only fills it in while it is still exactly what CMS.1 made.
"""

from typing import Any, Final

from wagtail.models import Locale

from apps.cms.models import StandardPage
from apps.cms.seed import seed_text
from apps.core.i18n import configured_languages, primary_language
from apps.seed import data
from apps.seed.demo_home import Texts, pick, publish, seeded_about

CONTACT: Final = data.PageSeed(
    slug={"en": "contact", "it": "contatti", "de": "kontakt"},
    title={"en": "Contact", "it": "Contatti", "de": "Kontakt"},
    body={
        "en": "<p>Questions about an order, a product or selling with us? Write to us and we "
        "answer within two working days.</p>",
        "it": "<p>Domande su un ordine, un prodotto o su come vendere con noi? Scrivici: "
        "rispondiamo entro due giorni lavorativi.</p>",
        "de": "<p>Fragen zu einer Bestellung, einem Produkt oder zum Verkauf bei uns? Schreiben "
        "Sie uns, wir antworten innerhalb von zwei Werktagen.</p>",
    },
)
SUPPORT_EMAIL: Final = f"support@{data.EMAIL_DOMAIN}"
AUTHORITY_EMAIL: Final = f"authorities@{data.EMAIL_DOMAIN}"
USER_CONTACT_URL: Final = f"https://{data.EMAIL_DOMAIN}/contact"


def contact_languages() -> list[str]:
    """The configured languages the contact texts exist in (the primary one if none)."""
    codes = [code for code in configured_languages() if code in CONTACT.body]
    return codes or [primary_language()]


CONTACT_LINES: Final[dict[str, Texts]] = {
    "support": {
        "en": "Customer support: {email}",
        "it": "Assistenza clienti: {email}",
        "de": "Kundenservice: {email}",
    },
    "hours": {
        "en": "Open Monday to Friday, 9 am to 5 pm.",
        "it": "Aperti dal lunedì al venerdì, dalle 9 alle 17.",
        "de": "Montag bis Freitag, 9 bis 17 Uhr.",
    },
}

ABOUT_EXTRA: Final[dict[str, Texts]] = {
    "story": {
        "en": "<p>Every shop here belongs to a small workshop that makes what it sells. We "
        "give them one place to be found, and keep the rules simple.</p>",
        "it": "<p>Ogni negozio qui appartiene a un piccolo laboratorio che produce ciò che "
        "vende. Diamo loro un posto in cui farsi trovare e teniamo semplici le regole.</p>",
        "de": "<p>Jeder Shop hier gehört einer kleinen Werkstatt, die herstellt, was sie "
        "verkauft. Wir geben ihr einen Ort, an dem man sie findet, und halten die Regeln "
        "einfach.</p>",
    },
    "figure_alt": {
        "en": "A workbench with tools and unfinished pieces",
        "it": "Un banco da lavoro con attrezzi e pezzi non finiti",
        "de": "Eine Werkbank mit Werkzeug und unfertigen Stücken",
    },
    "figure_caption": {
        "en": "Where the pieces start",
        "it": "Dove nascono i pezzi",
        "de": "Wo die Stücke entstehen",
    },
    "makers": {
        "en": "Makers on the marketplace",
        "it": "Artigiani sul marketplace",
        "de": "Hersteller auf dem Marktplatz",
    },
    "products": {
        "en": "Pieces in the catalogue",
        "it": "Pezzi a catalogo",
        "de": "Stücke im Katalog",
    },
    "video": {
        "en": "Watch how a workshop works",
        "it": "Guarda come lavora un laboratorio",
        "de": "So arbeitet eine Werkstatt",
    },
}
# An open-licence film on a video provider the video block accepts; it loads only after consent.
# Demo content only: "Big Buck Bunny", an open film by the Blender Foundation (CC BY 3.0).
VIDEO_URL: Final = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def about_body(language: str, images: dict[str, Any]) -> list[Any]:
    """The About page: the CMS.1 sentence, a story, a figure, two counters and a video link."""
    t = {key: pick(value, language) for key, value in ABOUT_EXTRA.items()}
    base = seed_text(language).about.body
    return [
        ("paragraph", base),
        ("paragraph", t["story"]),
        (
            "figure",
            {
                "image": images["how-it-works"],
                "alt_text": t["figure_alt"],
                "caption": t["figure_caption"],
            },
        ),
        ("counter", {"label": t["makers"], "value": len(data.VENDORS), "suffix": ""}),
        (
            "counter",
            {
                "label": t["products"],
                "value": sum(len(items) for items in data.PRODUCTS.values()),
                "suffix": "",
            },
        ),
        (
            "video_modal",
            {
                "title": t["video"],
                "poster": images["event-workshop"],
                "alt_text": t["video"],
                "url": VIDEO_URL,
            },
        ),
    ]


def contact_body(language: str) -> list[Any]:
    """The Contact page: the intro, the support address, the opening hours."""
    lines = {key: pick(value, language) for key, value in CONTACT_LINES.items()}
    email = f"<strong>{SUPPORT_EMAIL}</strong>"
    return [
        ("paragraph", pick(CONTACT.body, language)),
        (
            "paragraph",
            f"<p>{lines['support'].format(email=email)}</p><p>{lines['hours']}</p>",
        ),
    ]


def seed_about(run: Any, locales: dict[str, Locale], images: dict[str, Any]) -> None:
    """Enrich the About page of every language, unless an editor already changed its body."""
    for language, locale in locales.items():
        page = seeded_about(locale, language)
        if page is None:
            continue
        plain = [("paragraph", seed_text(language).about.body)]
        if _flat(page) != _flat_value(plain):
            continue  # not what CMS.1 made: an editor's page stays as it is
        page.body = about_body(language, images)
        publish(page)
        run.changes.update("cms.standardpage", page.url_path, {"body": "plain"}, {"body": "rich"})


def _flat_value(blocks: list[Any]) -> list[Any]:
    return [[kind, value] for kind, value in blocks]


def _flat(page: StandardPage) -> list[Any]:
    raw = page.body.get_prep_value()
    return [[item["type"], item["value"]] for item in raw]


def is_seed_contact(page: StandardPage) -> bool:
    """Whether `page` still has exactly the body the seed wrote in its language."""
    seeded = _flat_value(contact_body(page.locale.language_code))
    return _flat(page) == seeded
