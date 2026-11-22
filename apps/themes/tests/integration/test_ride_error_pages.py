"""THM.30: the `ride` 404 and 500 pages against the real stack (spec 14 §14.9)."""

import pytest
from django.http import HttpRequest
from django.test import Client, RequestFactory
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes import views

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]


@pytest.fixture(autouse=True)
def _ride(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.DEBUG = False


def test_the_500_page_runs_no_query(django_assert_num_queries) -> None:  # type: ignore[no-untyped-def]
    request: HttpRequest = RequestFactory().get("/broken/")
    with django_assert_num_queries(0):
        response = views.server_error(request)
    assert response.status_code == 500
    assert b"error-hero__code" in response.content


def test_the_404_page_has_the_hero_search_and_no_garage_menu(client: Client) -> None:
    response = client.get("/no-such-page-here/")
    html = response.content.decode()
    assert response.status_code == 404
    assert 'class="error-hero__code" aria-hidden="true">404<' in html
    assert 'id="error-search"' in html
    assert 'class="garage' not in html
