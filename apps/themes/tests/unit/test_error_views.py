"""THM.3: the 400 and 500 pages render with `base` without the database (spec 14 §14.6, §14.9).

A 500 is often caused by the database itself, and a 400 by a bad `Host` header that Wagtail's
site lookup would raise on again. Both handlers render the error layout with a plain context
(no request, no context processors) and the theme the request already resolved, or the
default theme: the unit suite blocks every query, so any database access fails here.
"""

import re
from unittest import mock

import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from django.urls import get_resolver
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes import views

HELP_URL = "/help/"


@pytest.fixture(autouse=True)
def _help_url(settings: SettingsWrapper) -> None:
    settings.MPMV_HELP_URL = HELP_URL
    settings.MPMV_THEME_DEFAULT = "base"


@pytest.fixture
def request_() -> HttpRequest:
    return RequestFactory().get("/broken/")


def test_the_project_routes_400_and_500_to_the_theme_handlers() -> None:
    resolver = get_resolver()
    handlers: dict[int, object] = {
        code: resolver.resolve_error_handler(code) for code in (400, 500)
    }
    assert handlers == {400: views.bad_request, 500: views.server_error}


def test_the_500_page_renders_with_base_without_the_database(request_: HttpRequest) -> None:
    response = views.server_error(request_)
    html = response.content.decode()
    assert response.status_code == 500
    assert 'data-theme="base"' in html
    assert '<link rel="stylesheet" href="/static/themes/base/theme.css"' in html
    assert re.search(r'<a class="skip-link" href="#main">', html)
    assert len(re.findall(r"<h1\b", html)) == 1
    assert '<footer class="site-footer">' in html


def test_the_500_page_keeps_the_help_link_in_the_header_and_footer(
    request_: HttpRequest,
) -> None:
    html = views.server_error(request_).content.decode()
    header = html.split('<header class="site-header">', 1)[1].split("</header>", 1)[0]
    footer = html.split('<footer class="site-footer">', 1)[1].split("</footer>", 1)[0]
    for part in (header, footer):
        assert f'<a class="help-link" href="{HELP_URL}">' in part


def test_the_500_page_has_no_form_and_no_database_menus(request_: HttpRequest) -> None:
    html = views.server_error(request_).content.decode()
    # No language switcher (it needs a CSRF token) and no CMS menus (they need the database).
    assert "<form" not in html
    assert 'id="site-nav"' not in html


def test_the_400_page_renders_with_base_without_the_database(request_: HttpRequest) -> None:
    response = views.bad_request(request_, Exception("bad host"))
    html = response.content.decode()
    assert response.status_code == 400
    assert 'data-theme="base"' in html
    assert len(re.findall(r"<h1\b", html)) == 1
    assert "<form" not in html


def test_a_broken_error_template_falls_back_to_plain_text(request_: HttpRequest) -> None:
    with mock.patch("apps.themes.views.loader.get_template", side_effect=RuntimeError("boom")):
        response = views.server_error(request_)
    assert response.status_code == 500
    assert b"Server Error (500)" in response.content
