"""THM.23: the demo shop home, the blog, the header menu and the footer of the demo seed.

Texts are invented. English uses British spelling and German standard German (no regional
variant); a language without its own text shows the source text. The CMS.19 vendor-recruitment
home is not built here: its body stays the landing, and this body lives on the demo shop page.
"""

import datetime as dt
from collections.abc import Callable
from typing import Any, Final

from wagtail.models import Locale, Site

from apps.cms.models import (
    BlogAuthor,
    BlogIndexPage,
    BlogPostPage,
    FooterColumn,
    FooterSettings,
    HomePage,
    NavigationMenu,
    SocialProfile,
    StandardPage,
)
from apps.core.i18n import primary_language

Texts = dict[str, str]
SOURCE: Final = "en"


def pick(texts: Texts, language: str) -> str:
    return texts.get(language) or texts[SOURCE]


BLOG_TITLE: Final[Texts] = {"en": "Blog", "it": "Blog", "de": "Blog"}
BLOG_SLUG: Final[Texts] = {"en": "blog", "it": "blog", "de": "blog"}
BLOG_INTRO: Final[Texts] = {
    "en": "<p>News from the workshops, with notes on materials and care.</p>",
    "it": "<p>Notizie dai laboratori, con appunti su materiali e cura.</p>",
    "de": "<p>Neuigkeiten aus den Werkstätten, mit Hinweisen zu Materialien und Pflege.</p>",
}
AUTHOR: Final = ("Demo editor", "demo-editor")
POSTS: Final[tuple[dict[str, Any], ...]] = (
    {
        "slug": {"en": "choosing-timber", "it": "scegliere-il-legno", "de": "holz-auswaehlen"},
        "title": {
            "en": "Choosing timber for the kitchen",
            "it": "Scegliere il legno per la cucina",
            "de": "Holz für die Küche auswählen",
        },
        "summary": {
            "en": "Which timbers cope with heat, water and daily use, and how to oil them.",
            "it": "Quali legni reggono calore, acqua e uso quotidiano, e come oliarli.",
            "de": "Welche Hölzer Hitze, Wasser und tägliche Nutzung vertragen und wie man sie ölt.",
        },
        "text": {
            "en": "<p>Hard timbers such as beech and walnut last longest. Oil a board once a "
            "month and it keeps its colour.</p>",
            "it": "<p>I legni duri come faggio e noce durano di più. Oliare un tagliere una volta "
            "al mese ne conserva il colore.</p>",
            "de": "<p>Harte Hölzer wie Buche und Nuss halten am längsten. Ein Brett einmal im "
            "Monat zu ölen erhält seine Farbe.</p>",
        },
        "date": dt.date(2026, 3, 2),
        "image": "home-hero",
    },
    {
        "slug": {"en": "glaze-and-colour", "it": "smalto-e-colore", "de": "glasur-und-farbe"},
        "title": {
            "en": "How a glaze gets its colour",
            "it": "Come lo smalto prende colore",
            "de": "Wie eine Glasur ihre Farbe bekommt",
        },
        "summary": {
            "en": "A short look at firing, metal oxides and why no two mugs match.",
            "it": "Uno sguardo a cottura e ossidi metallici, e a perché due tazze non sono uguali.",
            "de": "Ein kurzer Blick auf Brand, Metalloxide und warum keine zwei Tassen gleichen.",
        },
        "text": {
            "en": "<p>Each firing shifts the colour a little, so a catalogue photograph is a "
            "guide, not a promise.</p>",
            "it": "<p>Ogni cottura sposta un po' il colore: la foto del catalogo è una guida, "
            "non una promessa.</p>",
            "de": "<p>Jeder Brand verschiebt die Farbe ein wenig; das Katalogfoto ist ein "
            "Anhaltspunkt, kein Versprechen.</p>",
        },
        "date": dt.date(2026, 4, 14),
        "image": "how-it-works",
    },
)

MENU_LABELS: Final[dict[str, Texts]] = {
    "shop": {"en": "Shop", "it": "Negozio", "de": "Shop"},
    "blog": {"en": "Blog", "it": "Blog", "de": "Blog"},
    "about": {"en": "About", "it": "Chi siamo", "de": "Über uns"},
}
FOOTER_TITLE: Final[Texts] = {"en": "Explore", "it": "Esplora", "de": "Entdecken"}
SOCIAL: Final = (
    ("instagram", "https://www.instagram.com/mpmv.demo"),
    ("mastodon", "https://social.example/@mpmv_demo"),
)

HERO: Final[dict[str, Texts]] = {
    "heading": {
        "en": "Made by hand, sent from the workshop",
        "it": "Fatto a mano, spedito dal laboratorio",
        "de": "Handgemacht, direkt aus der Werkstatt",
    },
    "text": {
        "en": "Wood, ceramic and textile from small makers, each with its own shop.",
        "it": "Legno, ceramica e tessuti di piccoli artigiani, ognuno con il proprio negozio.",
        "de": "Holz, Keramik und Textilien von kleinen Manufakturen, jede mit eigenem Shop.",
    },
    "slide_two": {
        "en": "Meet the makers",
        "it": "Conosci gli artigiani",
        "de": "Die Macher kennenlernen",
    },
    "slide_three": {
        "en": "Fairs and workshops",
        "it": "Fiere e laboratori",
        "de": "Messen und Workshops",
    },
    "grid": {"en": "Newest in wood", "it": "Novità in legno", "de": "Neu in Holz"},
    "quotes": {
        "en": "Kind words from customers",
        "it": "Parole dei clienti",
        "de": "Stimmen unserer Kundschaft",
    },
    "cta": {
        "en": "Read the stories behind the pieces",
        "it": "Leggi le storie dietro ai pezzi",
        "de": "Die Geschichten hinter den Stücken",
    },
    "cta_text": {
        "en": "Our blog shows how the pieces are made and cared for.",
        "it": "Il blog mostra come i pezzi vengono fatti e curati.",
        "de": "Der Blog zeigt, wie die Stücke entstehen und gepflegt werden.",
    },
    "blog": {"en": "From the blog", "it": "Dal blog", "de": "Aus dem Blog"},
    "vendors": {"en": "Our makers", "it": "I nostri artigiani", "de": "Unsere Macher"},
}
QUOTES: Final = (
    (
        {
            "en": "Fast delivery and a kind note in the box.",
            "it": "Consegna rapida e un biglietto gentile nella scatola.",
            "de": "Schnelle Lieferung und eine nette Karte im Paket.",
        },
        "Demo customer A",
    ),
    (
        {
            "en": "Good prices from small workshops.",
            "it": "Buoni prezzi da piccoli laboratori.",
            "de": "Gute Preise von kleinen Werkstätten.",
        },
        "Demo customer B",
    ),
)


def _translate(page: Any, locale: Locale, fill: Callable[[Any, str], None], language: str) -> Any:
    existing = page.get_translation_or_none(locale)
    if existing is not None:
        return existing.specific
    copy = page.copy_for_translation(locale).specific
    fill(copy, language)
    copy.save_revision().publish()
    copy.refresh_from_db()
    return copy


def _publish(page: Any) -> Any:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def seed_blog(
    run: Any, home: HomePage, locales: dict[str, Locale], images: dict[str, Any]
) -> tuple[BlogIndexPage, list[BlogPostPage]]:
    """The blog index and two articles in the source language, each translated into every other
    configured language."""
    source = primary_language()
    locale = home.locale
    index = BlogIndexPage.objects.child_of(home).first()
    if index is None:
        index = BlogIndexPage(
            title=pick(BLOG_TITLE, source), slug=BLOG_SLUG["en"], intro=pick(BLOG_INTRO, source)
        )
        home.add_child(instance=index)
        index = _publish(index)
        run.created("cms.blogindexpage", index.url_path)
    author = BlogAuthor.objects.filter(slug=AUTHOR[1], locale=locale).first()
    if author is None:
        author = BlogAuthor.objects.create(name=AUTHOR[0], slug=AUTHOR[1], locale=locale)
        run.created("cms.blogauthor", author.slug)
    posts: list[BlogPostPage] = []
    for spec in POSTS:
        post = BlogPostPage.objects.child_of(index).filter(slug=spec["slug"]["en"]).first()
        if post is None:
            post = BlogPostPage(
                title=pick(spec["title"], source),
                slug=spec["slug"]["en"],
                date=spec["date"],
                author=author,
                summary=pick(spec["summary"], source),
                image=images[spec["image"]],
                image_alt_text=pick(spec["title"], source),
                body=[("paragraph", pick(spec["text"], source))],
            )
            index.add_child(instance=post)
            post = _publish(post)
            run.created("cms.blogpostpage", post.url_path)
        posts.append(post)

    def fill_index(copy: Any, language: str) -> None:
        copy.title = copy.draft_title = pick(BLOG_TITLE, language)
        copy.intro = pick(BLOG_INTRO, language)

    for language, other in locales.items():
        if other.pk == locale.pk:
            continue
        translated = _translate(index, other, fill_index, language)
        for post, spec in zip(posts, POSTS, strict=True):

            def fill_post(copy: Any, lang: str, spec: dict[str, Any] = spec) -> None:
                copy.title = copy.draft_title = pick(spec["title"], lang)
                copy.summary = pick(spec["summary"], lang)
                copy.image_alt_text = pick(spec["title"], lang)
                copy.body = [("paragraph", pick(spec["text"], lang))]

            _translate(post, other, fill_post, language)
        _ = translated
    return index, posts


def _local(page: Any, locale: Locale) -> Any:
    found = page.get_translation_or_none(locale)
    return found.specific if found is not None else page


def seed_navigation(
    run: Any,
    locales: dict[str, Locale],
    pages: list[tuple[str, Any]],
) -> None:
    """The header menu of every language (`pages` is (label key, source page)) and the footer."""
    for language, locale in locales.items():
        menu = NavigationMenu.objects.filter(locale=locale, location="header").first()
        if menu is None or len(menu.links) != 1 or menu.links[0].value["label"]:
            continue  # an editor's menu stays as it is
        about = menu.links[0].value["page"]  # the CMS.1 About page
        entries = [*pages, ("about", about)]
        menu.links = [
            ("page", {"page": _local(page, locale), "label": pick(MENU_LABELS[key], language)})
            for key, page in entries
        ]
        menu.save()
        run.created("cms.navigationmenu", f"header {language}")
    site = Site.objects.get(is_default_site=True)
    footer = FooterSettings.for_site(site)
    for language, locale in locales.items():
        if footer.columns.filter(language=language).exists():
            continue
        FooterColumn.objects.create(
            settings=footer,
            language=language,
            title=pick(FOOTER_TITLE, language),
            links=[
                (
                    "page",
                    {"page": _local(page, locale), "label": pick(MENU_LABELS[key], language)},
                )
                for key, page in pages
            ],
        )
        run.created("cms.footercolumn", language)
    for network, url in SOCIAL:
        if not footer.profiles.filter(network=network).exists():
            SocialProfile.objects.create(settings=footer, network=network, url=url)
            run.created("cms.socialprofile", network)


def shop_home_body(
    language: str,
    images: dict[str, Any],
    *,
    how: StandardPage,
    blog: BlogIndexPage,
    posts: list[Any],
    vendors: list[Any],
    term: Any,
    notice: str,
) -> list[Any]:
    """The demo home: a slider, the newest products of one term, quotes, a call to action, the
    blog teaser and the vendors, in the blocks the editor has."""
    t = {key: pick(value, language) for key, value in HERO.items()}
    slide: dict[str, Any] = {"links": [], "image": None, "alt_text": "", "text": ""}
    link = {"page": blog, "url": "", "label": pick(BLOG_TITLE, language)}
    return [
        (
            "hero",
            {
                "heading": t["heading"],
                "text": t["text"],
                "image": images["home-hero"],
                "alt_text": t["heading"],
                "layout": "slider",
                "slides": [
                    {
                        **slide,
                        "image": images["how-it-works"],
                        "alt_text": t["slide_two"],
                        "heading": t["slide_two"],
                        "links": [
                            {"page": how, "url": "", "label": pick(MENU_LABELS["about"], language)}
                        ],
                    },
                    {
                        **slide,
                        "image": images["event-fair"],
                        "alt_text": t["slide_three"],
                        "heading": t["slide_three"],
                    },
                ],
                "links": [],
            },
        ),
        ("paragraph", notice),
        (
            "product_grid",
            {"heading": t["grid"], "source": [("term", term)], "limit": 4, "see_all": True},
        ),
        (
            "strip",
            {
                "heading": t["quotes"],
                "cards": [
                    ("testimonial", {"quote": pick(quote, language), "author": author, "role": ""})
                    for quote, author in QUOTES
                ],
                "per_view": "2",
            },
        ),
        ("cta", {"heading": t["cta"], "text": t["cta_text"], "link": link}),
        (
            "strip",
            {
                "heading": t["blog"],
                "cards": [("page", {"page": post, "label": "", "text": ""}) for post in posts],
                "per_view": "2",
            },
        ),
        (
            "strip",
            {
                "heading": t["vendors"],
                "cards": [
                    ("vendor", {"vendor": vendor, "label": "", "text": ""}) for vendor in vendors
                ],
                "per_view": "4",
            },
        ),
    ]
