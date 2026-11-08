"""CMS.1: home and static pages of every configured language, served by Wagtail.

`/` is the EN tree, `/it/`, `/de/`, `/fr/` and `/es/` the translated trees (I18N.1); pages render
under the strict CSP of SEC.1 (no inline scripts or styles), and the language switcher leads to
the same page in the other language even when its slug is translated.
"""

import re

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from wagtail.models import Site

from apps.cms.seed import seed_text
from apps.cms.services import SeedReport
from apps.cms.tests.factories import StandardPageFactory
from apps.core.i18n import configured_languages

pytestmark = pytest.mark.django_db


def _prefix(language: str) -> str:
    return "/" if language == settings.LANGUAGE_CODE else f"/{language}/"


def _about_path(language: str) -> str:
    return f"{_prefix(language)}{seed_text(language).about.slug}/"


# (path, language, title): the seeded home and About page of every configured language.
PAGES = [
    page
    for code in configured_languages()
    for page in (
        (_prefix(code), code, seed_text(code).home.title),
        (_about_path(code), code, seed_text(code).about.title),
    )
]
ABOUT_IN_EACH_LANGUAGE = {code: _about_path(code) for code in configured_languages()}
HOME_IN_EACH_LANGUAGE = {code: _prefix(code) for code in configured_languages()}


def test_the_go_live_languages_are_all_requested() -> None:
    assert ("/it/chi-siamo/", "it", "Chi siamo") in PAGES
    assert ("/de/ueber-uns/", "de", "Über uns") in PAGES
    assert ("/fr/qui-sommes-nous/", "fr", "Qui sommes-nous") in PAGES
    assert ("/es/quienes-somos/", "es", "Quiénes somos") in PAGES


@pytest.mark.parametrize(("path", "language", "title"), PAGES)
def test_each_language_serves_its_home_and_static_page(
    client: Client, seeded: SeedReport, path: str, language: str, title: str
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response["Content-Language"] == language
    html = response.content.decode()
    assert f'<html lang="{language}"' in html
    assert f"<h1>{title}</h1>" in html


def test_the_italian_home_shows_italian_content(client: Client, seeded: SeedReport) -> None:
    html = client.get("/it/").content.decode()
    assert "Prodotti di artigiani indipendenti" in html
    assert "Products from independent makers" not in html


@pytest.mark.parametrize("path", ["/it/about/", "/de/chi-siamo/", "/chi-siamo/", "/missing/"])
def test_a_page_is_found_only_in_its_own_language_tree(
    client: Client, seeded: SeedReport, path: str
) -> None:
    assert client.get(path).status_code == 404


def test_draft_pages_are_not_served(client: Client, seeded: SeedReport) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Draft", parent=home, live=False)
    assert client.get("/draft/").status_code == 404


def test_the_home_url_name_is_the_cms_home(client: Client, seeded: SeedReport) -> None:
    assert client.get(reverse("home")).status_code == 200


@pytest.mark.parametrize(("path", "_language", "_title"), PAGES)
def test_pages_render_under_the_strict_csp(
    client: Client, seeded: SeedReport, path: str, _language: str, _title: str
) -> None:
    response = client.get(path)
    policy = response["Content-Security-Policy"]
    assert "'unsafe-inline'" not in policy
    html = response.content.decode()
    # Only same-origin script files (THM.3: vendored htmx and Alpine, the theme module).
    assert all("src=" in tag for tag in re.findall(r"<script\b[^>]*>", html))
    assert "<style" not in html
    assert not re.search(r"\sstyle=", html)
    assert re.search(r'<link rel="stylesheet" href="/static/themes/base/theme\.css', html)


@pytest.mark.parametrize(
    ("path", "targets"),
    [
        *((about, ABOUT_IN_EACH_LANGUAGE) for about in ABOUT_IN_EACH_LANGUAGE.values()),
        *((home, HOME_IN_EACH_LANGUAGE) for home in HOME_IN_EACH_LANGUAGE.values()),
    ],
)
def test_the_switcher_offers_the_same_page_in_each_language(
    client: Client, seeded: SeedReport, path: str, targets: dict[str, str]
) -> None:
    html = client.get(path).content.decode()
    for code, target in targets.items():
        assert re.search(
            rf'name="next" value="{re.escape(target)}">\s*<button type="submit" '
            rf'name="language" value="{code}"',
            html,
        ), f"no switch to {code} leading to {target}"


def test_switching_language_on_a_static_page_follows_the_translated_slug(
    client: Client, seeded: SeedReport
) -> None:
    response = client.post(
        reverse("switch_language"), {"language": "it", "next": "/it/chi-siamo/"}, follow=True
    )
    assert response.redirect_chain[-1][0] == "/it/chi-siamo/"
    assert "<h1>Chi siamo</h1>" in response.content.decode()


def test_a_page_without_translation_switches_to_the_other_languages_home(
    client: Client, seeded: SeedReport
) -> None:
    home = Site.objects.get(is_default_site=True).root_page
    StandardPageFactory.create(title="Only in English", parent=home)
    html = client.get("/only-in-english/?page=2").content.decode()
    assert 'name="next" value="/it/?page=2"' in html
    assert 'name="next" value="/only-in-english/?page=2"' in html
