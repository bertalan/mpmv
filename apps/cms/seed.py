"""Sample texts for the seed pages (CMS.1): synthetic content for development, STAGE and e2e.

Legal placeholders (I18N.9): the five customer legal and checkout pages in every planned
language, with a title and a sentence saying the text is not written yet. They are never legal
texts: those are human work (AI draft, native-speaker review, legal counsel check, Q-42).

Pure data, no ORM. A language without an entry gets the primary language's texts, as an
untranslated Wagtail copy would; adding a language never needs an entry here. The five planned
languages (EN, IT, DE, FR, ES; I18N.1) have entries whether or not settings lists them yet.
"""

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class PageText:
    title: str
    body: str  # rich text (HTML from the editor's allowed features)
    slug: str = ""  # empty: slugified title


@dataclass(frozen=True)
class SiteTexts:
    home: PageText
    about: PageText


SEED_TEXTS: dict[str, SiteTexts] = {
    "en": SiteTexts(
        home=PageText(
            title="Home",
            slug="home",
            body="<p>Products from independent makers, shipped from their workshops.</p>",
        ),
        about=PageText(
            title="About us",
            slug="about",
            body="<p>We are a marketplace of independent vendors.</p>",
        ),
    ),
    "it": SiteTexts(
        home=PageText(
            title="Home",
            slug="home",
            body="<p>Prodotti di artigiani indipendenti, spediti dai loro laboratori.</p>",
        ),
        about=PageText(
            title="Chi siamo",
            slug="chi-siamo",
            body="<p>Siamo un marketplace di venditori indipendenti.</p>",
        ),
    ),
    "de": SiteTexts(
        home=PageText(
            title="Startseite",
            slug="home",
            body="<p>Produkte unabhängiger Hersteller, direkt aus ihren Werkstätten.</p>",
        ),
        about=PageText(
            title="Über uns",
            slug="ueber-uns",
            body="<p>Wir sind ein Marktplatz unabhängiger Händler.</p>",
        ),
    ),
    "fr": SiteTexts(
        home=PageText(
            title="Accueil",
            slug="home",
            body="<p>Des produits de fabricants indépendants, expédiés depuis leurs ateliers.</p>",
        ),
        about=PageText(
            title="Qui sommes-nous",
            slug="qui-sommes-nous",
            body="<p>Nous sommes une place de marché de vendeurs indépendants.</p>",
        ),
    ),
    "es": SiteTexts(
        home=PageText(
            title="Inicio",
            slug="home",
            body="<p>Productos de artesanos independientes, enviados desde sus talleres.</p>",
        ),
        about=PageText(
            title="Quiénes somos",
            slug="quienes-somos",
            body="<p>Somos un mercado de vendedores independientes.</p>",
        ),
    ),
}


def seed_text(language: str) -> SiteTexts:
    """The sample texts of `language`, or of the primary language (EN) when it has none."""
    return SEED_TEXTS.get(language) or SEED_TEXTS.get(settings.LANGUAGE_CODE) or SEED_TEXTS["en"]


# --- Legal placeholders (I18N.9): (title, slug) per page kind, and the placeholder sentence.

LEGAL_PLACEHOLDER_TITLES: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "terms": ("Terms of sale", "terms-of-sale"),
        "privacy": ("Privacy policy", "privacy-policy"),
        "withdrawal": ("Right of withdrawal", "right-of-withdrawal"),
        "shipping": ("Shipping", "shipping"),
        "returns": ("Returns", "returns"),
        "reviews": ("How reviews are checked", "how-reviews-are-checked"),
    },
    "it": {
        "terms": ("Condizioni di vendita", "condizioni-di-vendita"),
        "privacy": ("Informativa sulla privacy", "informativa-privacy"),
        "withdrawal": ("Diritto di recesso", "diritto-di-recesso"),
        "shipping": ("Spedizioni", "spedizioni"),
        "returns": ("Resi", "resi"),
        "reviews": ("Come verifichiamo le recensioni", "come-verifichiamo-le-recensioni"),
    },
    "de": {
        "terms": ("Verkaufsbedingungen", "verkaufsbedingungen"),
        "privacy": ("Datenschutzerklärung", "datenschutz"),
        "withdrawal": ("Widerrufsrecht", "widerrufsrecht"),
        "shipping": ("Versand", "versand"),
        "returns": ("Rücksendungen", "ruecksendungen"),
        "reviews": ("So prüfen wir Bewertungen", "bewertungen-pruefung"),
    },
    "fr": {
        "terms": ("Conditions générales de vente", "conditions-generales-de-vente"),
        "privacy": ("Politique de confidentialité", "confidentialite"),
        "withdrawal": ("Droit de rétractation", "droit-de-retractation"),
        "shipping": ("Livraison", "livraison"),
        "returns": ("Retours", "retours"),
        "reviews": ("Comment les avis sont vérifiés", "verification-des-avis"),
    },
    "es": {
        "terms": ("Condiciones de venta", "condiciones-de-venta"),
        "privacy": ("Política de privacidad", "privacidad"),
        "withdrawal": ("Derecho de desistimiento", "desistimiento"),
        "shipping": ("Envíos", "envios"),
        "returns": ("Devoluciones", "devoluciones"),
        "reviews": ("Cómo se comprueban las reseñas", "verificacion-de-resenas"),
    },
}

LEGAL_PLACEHOLDER_BODY: dict[str, str] = {
    "en": "<p><b>Placeholder.</b> This page has no legal text yet. It is not in force and must "
    "not be relied on.</p>",
    "it": "<p><b>Segnaposto.</b> Questa pagina non ha ancora un testo legale. Non è in vigore e "
    "non va considerata valida.</p>",
    "de": "<p><b>Platzhalter.</b> Diese Seite hat noch keinen Rechtstext. Sie gilt nicht und ist "
    "nicht verbindlich.</p>",
    "fr": "<p><b>Texte provisoire.</b> Cette page n'a pas encore de texte juridique. Elle n'est "
    "pas en vigueur et n'a aucune valeur.</p>",
    "es": "<p><b>Texto provisional.</b> Esta página aún no tiene texto legal. No está en vigor y "
    "no tiene validez.</p>",
}


# OP-28 (Omnibus): the draft paragraph of the reviews page, in EN and IT. It is a starting point
# for the owner and counsel, who review it like every legal page (native review, counsel check)
# before go-live; the other languages keep the placeholder until they are translated.
REVIEWS_POLICY_BODY: dict[str, str] = {
    "en": "<p>A review carries the label <b>Verified purchase</b> only when it comes from the "
    "customer account that bought that product on an order that was paid and not cancelled or "
    "refunded. We check this ourselves against our order records: nobody can mark their own "
    "review as verified.</p><p>Reviews carried over from our previous shop are shown, with "
    "their original date, under the label <b>Not verified</b>, because we cannot tie them to "
    "an order, unless we found an order of the same customer for the same product. A review "
    "with that label is not hidden and is not a bad review: it only lacks proof of "
    "purchase.</p>",
    "it": "<p>Una recensione ha l'etichetta <b>Acquisto verificato</b> solo se arriva "
    "dall'account cliente che ha comprato quel prodotto con un ordine pagato e non annullato né "
    "rimborsato. Il controllo lo facciamo noi sui dati degli ordini: nessuno può verificare da "
    "sé la propria recensione.</p><p>Le recensioni trasferite dal nostro vecchio negozio sono "
    "visibili, con la data originale, con l'etichetta <b>Non verificata</b>, perché non "
    "possiamo collegarle a un ordine, salvo quando abbiamo trovato un ordine dello stesso "
    "cliente per lo stesso prodotto. Una recensione con questa etichetta non è nascosta e non "
    "è una recensione negativa: manca solo la prova d'acquisto.</p>",
}


def legal_placeholder(language: str, kind: str) -> PageText:
    """The placeholder of one legal page in `language`, or in EN when it has none."""
    titles = LEGAL_PLACEHOLDER_TITLES.get(language) or LEGAL_PLACEHOLDER_TITLES["en"]
    body = LEGAL_PLACEHOLDER_BODY.get(language) or LEGAL_PLACEHOLDER_BODY["en"]
    if kind == "reviews" and language in REVIEWS_POLICY_BODY:
        body = REVIEWS_POLICY_BODY[language]
    title, slug = titles[kind]
    return PageText(title=title, body=body, slug=slug)
