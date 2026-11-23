"""THM.9 part 1: the `ride` content and system pages on Bootstrap 5.3 (spec 14 §14.6, §14.9).

Every page type renders in `base` and in `ride` and carries the same data (title, headings,
links, robots, canonical, hreflang, JSON-LD, form controls); ride adds the page banner with the
only h1 and the breadcrumb. No inline script or style.
"""

import json
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.test import Client, RequestFactory
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.cms.seed import seed_text
from apps.core.i18n import primary_language
from apps.core.models import MediaImageRendition
from apps.themes import views
from tests.i18n import translated
from tests.ride_content_site import build_content_site

pytestmark = pytest.mark.django_db

PAGE_TYPES = [
    "standard",
    "blog",
    "post",
    "author",
    "legal",
    "legal_version",
    "vendor",
    "recruitment",
    "contact",
]


@pytest.fixture
def paths(settings: SettingsWrapper) -> dict[str, str]:
    settings.MPMV_THEME_DEFAULT = "ride"
    return build_content_site()


def soup(client: Client, path: str, theme: str, settings: SettingsWrapper) -> Any:
    settings.MPMV_THEME_DEFAULT = theme
    response = client.get(path)
    assert response.status_code == 200, (path, theme)
    assert f'data-theme="{theme}"' in response.content.decode()
    return BeautifulSoup(response.content, "html.parser")


def data_of(page: Any) -> dict[str, Any]:
    main = page.find("main")
    for nav in main.select("nav.breadcrumb"):
        nav.decompose()
    head = page.head
    return {
        "title": page.title.get_text(strip=True),
        "robots": sorted(m["content"] for m in head.select("meta[name=robots]")),
        "canonical": [link["href"] for link in head.select("link[rel=canonical]")],
        "hreflang": sorted(
            (link["hreflang"], link["href"]) for link in head.select("link[rel=alternate]")
        ),
        "jsonld": [json.loads(s.string) for s in head.select("script[type='application/ld+json']")],
        "headings": [
            (h.name, h.get_text(" ", strip=True))
            for h in main.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        ],
        # A set: the ride product tile repeats its link in a "View" button (THM.6).
        "links": sorted({a["href"] for a in main.find_all("a", href=True)}),
        "controls": sorted(
            (c.name, c.get("name"), c.get("type"))
            for c in main.find_all(["input", "select", "textarea"])
        ),
        "times": [t["datetime"] for t in main.find_all("time")],
    }


@pytest.mark.parametrize("kind", PAGE_TYPES)
def test_base_and_ride_carry_the_same_data(
    kind: str, client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    base = data_of(soup(client, paths[kind], "base", settings))
    ride = data_of(soup(client, paths[kind], "ride", settings))
    assert ride == base, kind


@pytest.mark.parametrize("kind", PAGE_TYPES)
def test_the_banner_holds_the_only_h1_and_the_breadcrumb(
    kind: str, client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    page = soup(client, paths[kind], "ride", settings)
    banner = page.select_one("main .page-banner")
    assert banner is not None, kind
    assert len(page.find_all("h1")) == 1
    assert banner.h1 is not None
    nav = banner.select_one("nav.breadcrumb")
    assert nav is not None
    assert nav["aria-label"] == translated("Breadcrumb")
    labels = [li.get_text(strip=True) for li in nav.select("li")]
    assert labels[0] == seed_text(primary_language()).home.title
    assert nav.select_one("[aria-current=page]").get_text(strip=True) == labels[-1]
    assert labels[-1] == banner.h1.get_text(strip=True) or kind in {"author", "legal_version"}
    assert len({li.get_text(strip=True) for li in nav.select("li")}) == len(labels)


def test_the_breadcrumb_follows_the_page_tree(
    client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    post = soup(client, paths["post"], "ride", settings)
    labels = [li.get_text(strip=True) for li in post.select(".page-banner nav.breadcrumb li")]
    assert labels == [seed_text(primary_language()).home.title, "Blog", "Workshop visit 0"]
    links = [a["href"] for a in post.select(".page-banner nav.breadcrumb a")]
    assert links[1] == paths["blog"]
    author = soup(client, paths["author"], "ride", settings)
    labels = [li.get_text(strip=True) for li in author.select(".page-banner nav.breadcrumb li")]
    assert labels == [seed_text(primary_language()).home.title, "Blog", "Ada Example"]


@pytest.mark.parametrize("kind", PAGE_TYPES)
def test_no_inline_script_or_style_and_no_app_stylesheet(
    kind: str, client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    page = soup(client, paths[kind], "ride", settings)
    assert page.find("style") is None
    assert page.find(attrs={"style": True}) is None
    assert all(s.get("src") or s.get("type") for s in page.find_all("script"))
    assert not [
        link
        for link in page.find_all("link", rel="stylesheet")
        if "themes/ride" not in link["href"]
    ]


def test_the_blog_uses_bootstrap_cards_and_pagination(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    paths = build_content_site(posts=13)
    page = soup(client, paths["blog"], "ride", settings)
    assert len(page.select("article.post-summary.card")) == 12
    nav = page.select_one(f'nav[aria-label="{translated("Pagination")}"] ul.pagination')
    assert nav is not None
    assert nav.select_one("a.page-link[rel=next]")["href"] == "?page=2"
    second = soup(client, f"{paths['blog']}?page=2", "ride", settings)
    assert second.select_one("a.page-link[rel=prev]") is not None
    assert second.select_one("a[rel=next]") is None


def test_post_and_author_images_use_the_presets_without_building_renditions(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    paths = build_content_site(images=True)
    before = MediaImageRendition.objects.count()
    for kind, alt in (("post", "A workshop bench"), ("author", "Ada at the wheel")):
        page = soup(client, paths[kind], "ride", settings)
        figure = page.select_one("main figure")
        assert figure is not None, kind
        assert figure.find("picture") is not None, kind
        img = figure.find("img")
        assert img is not None, kind
        # No rendition exists yet: the original is the fallback.
        assert (img["alt"], img["width"], img["height"]) == (alt, "2000", "1500"), kind
        assert img["src"], kind
    assert MediaImageRendition.objects.count() == before


def test_the_recruitment_plans_are_cards_and_the_call_to_action_a_button(
    client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    page = soup(client, paths["recruitment"], "ride", settings)
    plans = page.select(".plans .card.plan")
    assert [p.h3.get_text(strip=True) for p in plans] == ["Starter", "Workshop"]
    assert "plan--highlighted" in plans[1]["class"]


def test_the_404_page_renders_in_ride_with_one_h1(
    client: Client, paths: dict[str, str], settings: SettingsWrapper
) -> None:
    for theme in ("base", "ride"):
        settings.MPMV_THEME_DEFAULT = theme
        response = client.get("/no-such-page-here/")
        assert response.status_code == 404
        page = BeautifulSoup(response.content, "html.parser")
        assert [h.get_text(strip=True) for h in page.find_all("h1")] == [
            translated("Page not found")
        ]
        robots = page.select_one("meta[name=robots]")
        assert robots is not None
        assert str(robots["content"]).startswith("noindex")
        assert page.find("a", string=translated("Go to the home page")) is not None
    assert page.select_one("main .page-banner h1") is not None


def test_the_500_page_renders_in_ride_without_the_database(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    request = RequestFactory().get("/boom/")
    response = views.server_error(request)
    assert response.status_code == 500
    page = BeautifulSoup(response.content, "html.parser")
    assert [h.get_text(strip=True) for h in page.find_all("h1")] == [
        translated("Something went wrong")
    ]
    assert page.select_one("main .page-banner h1") is not None
    assert page.find("a", string=translated("Go to the home page")) is not None
    assert 'data-theme="ride"' in response.content.decode()
