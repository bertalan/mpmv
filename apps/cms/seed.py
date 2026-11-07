"""Sample texts for the seed pages (CMS.1): synthetic content for development, STAGE and e2e.

Pure data, no ORM. A language without an entry gets the primary language's texts, as an
untranslated Wagtail copy would; adding a language never needs an entry here.
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
}


def seed_text(language: str) -> SiteTexts:
    """The sample texts of `language`, or of the primary language (EN) when it has none."""
    return SEED_TEXTS.get(language) or SEED_TEXTS.get(settings.LANGUAGE_CODE) or SEED_TEXTS["en"]
