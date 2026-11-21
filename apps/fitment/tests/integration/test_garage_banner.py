"""FIT.7 (3/n): the garage banner on the lists (spec 17 §17.4.4).

The banner names the active vehicle with *Change* and *Show all*; *Show all* posts through the
chooser's apply view and returns by the Referer; the page URL and any `next` are never written into
the markup; error pages and the generic state show nothing; the count waits for FIT.8.
"""

import re
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from apps.catalog.tests.factories import CategoryFactory, CategoryTranslationFactory
from apps.core.i18n import primary_language
from apps.core.models import TranslationStatus
from apps.fitment.tests.factories import FitmentNodeFactory

pytestmark = pytest.mark.django_db

LISTING = "/?s=zzz"  # the search page answers on the language home


def _t(msgid: str) -> str:
    with translation.override(primary_language()):
        return translation.gettext(msgid)


@pytest.fixture(autouse=True)
def vehicles() -> None:
    make = FitmentNodeFactory.create(name="Acme")
    FitmentNodeFactory.create(name="Roadster", parent=make)
    FitmentNodeFactory.create(name="Tourer", parent=make)


def _save(client: Client, model: str) -> Any:
    return client.post(
        reverse("garage_save"), {"make": "Acme", "model": model, "year": "", "next": "/"}
    )


def _banner(html: str) -> str:
    match = re.search(r"<section class=\"garage-banner.*?</section>", html, re.S)
    assert match, "the list carries no garage banner"
    return match.group(0)


def test_a_list_names_the_active_vehicle_with_change_and_show_all(client: Client) -> None:
    _save(client, "Roadster")
    banner = _banner(client.get(LISTING).content.decode())
    assert 'role="status"' in banner
    assert 'aria-label="Garage"' in banner or "aria-label=" in banner
    assert _t("Showing items for: %(label)s") % {"label": "Acme, Roadster"} in banner
    assert f'href="{reverse("garage_choose")}"' in banner
    assert _t("Change") in banner
    assert f'action="{reverse("garage_apply")}"' in banner
    assert 'name="choice" value="generic"' in banner
    assert _t("Show all") in banner
    assert "csrfmiddlewaretoken" in banner


def test_the_banner_shows_no_count_yet(client: Client) -> None:
    """The "N of M items" count comes with the filter (FIT.8)."""
    _save(client, "Roadster")
    banner = _banner(client.get(LISTING).content.decode())
    assert not re.search(r"\d", re.sub(r'csrfmiddlewaretoken" value="[^"]*"', "", banner))


def test_no_banner_without_a_vehicle_or_when_generic(client: Client) -> None:
    assert "data-garage-banner" not in client.get(LISTING).content.decode()
    _save(client, "Roadster")
    client.post(reverse("garage_apply"), {"choice": "generic"})
    assert "data-garage-banner" not in client.get(LISTING).content.decode()


def test_show_all_returns_to_the_list_by_the_referer_and_resets(client: Client) -> None:
    _save(client, "Roadster")
    response = client.post(
        reverse("garage_apply"), {"choice": "generic"}, headers={"Referer": "/?s=zzz"}
    )
    assert response["Location"] == "/?s=zzz"
    assert "data-garage-banner" not in client.get(LISTING).content.decode()
    foreign = client.post(
        reverse("garage_apply"), {"choice": "generic"}, headers={"Referer": "https://evil.example/"}
    )
    assert foreign["Location"] == "/"  # a foreign Referer is dropped


def test_an_explicit_next_still_wins_over_the_referer(client: Client) -> None:
    _save(client, "Roadster")
    response = client.post(
        reverse("garage_apply"),
        {"choice": "generic", "next": "/shop/"},
        headers={"Referer": "/other/"},
    )
    assert response["Location"] == "/shop/"


def test_the_banner_never_writes_the_page_url_or_a_next(client: Client) -> None:
    """A page URL can hold a secret token: neither control echoes it."""
    _save(client, "Roadster")
    html = client.get("/?s=zzz&token=top-secret-marker").content.decode()
    banner = _banner(html)
    assert "next" not in banner
    assert "top-secret-marker" not in banner
    assert "zzz" not in banner


def test_an_error_page_shows_no_banner_and_runs_no_garage_query(client: Client) -> None:
    _save(client, "Roadster")
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/no-such-page-here/")
    assert response.status_code == 404
    assert "data-garage-banner" not in response.content.decode()
    assert not [q for q in queries.captured_queries if "fitment_" in q["sql"]]


def test_the_banner_adds_no_query_to_the_header_menu(client: Client) -> None:
    _save(client, "Roadster")
    _save(client, "Tourer")
    client.get(LISTING)  # warm caches
    with CaptureQueriesContext(connection) as with_banner:
        client.get(LISTING)
    fitment = [q for q in with_banner.captured_queries if "fitment_" in q["sql"]]
    assert len(fitment) <= 7  # the state is read once per request, banner and menu share it


def test_the_banner_shows_on_a_category_listing_too(client: Client) -> None:
    category = CategoryFactory.create()
    CategoryTranslationFactory.create(
        category=category, slug="bags", status=TranslationStatus.PUBLISHED
    )
    _save(client, "Tourer")
    from tests.i18n import category_url

    html = client.get(category_url(primary_language(), "bags")).content.decode()
    assert _t("Showing items for: %(label)s") % {"label": "Acme, Tourer"} in _banner(html)
