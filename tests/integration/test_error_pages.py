"""THM.3: error pages, the footer and the content shell of `base` (spec 14 §14.6, §14.9).

The 400, 403, 404, 500 and CSRF failure pages render with the `base` layouts under the strict
CSP, with one `<h1>`, the skip link and the help link. 403, 404 and the CSRF failure keep the
full header and footer (menus, language switcher); 500 never touches the database. The footer
shows the CMS.1 footer menu, and the standard page uses the content shell.
"""

import re

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory
from django.urls import reverse
from django.views import defaults
from pytest_django import DjangoAssertNumQueries
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.cms.services import seed_site
from apps.themes import views
from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db

HELP_URL = "/help/"


@pytest.fixture(autouse=True)
def site(settings: SettingsWrapper) -> None:
    settings.MPMV_HELP_URL = HELP_URL
    settings.MPMV_THEME_DEFAULT = "base"
    seed_site()


def part(html: str, tag: str) -> str:
    return html.split(f'<{tag} class="site-{tag}">', 1)[1].split(f"</{tag}>", 1)[0]


def assert_base_page(html: str) -> None:
    assert 'data-theme="base"' in html
    assert '<link rel="stylesheet" href="/static/themes/base/theme.css"' in html
    assert '<a class="skip-link" href="#main">' in html
    assert len(re.findall(r"<h1\b", html)) == 1
    for tag in ("header", "footer"):
        assert f'<a class="help-link" href="{HELP_URL}">' in part(html, tag)
    assert "<style" not in html
    assert not re.search(r"\sstyle=", html)


def assert_full_chrome(html: str) -> None:
    assert 'id="site-nav"' in part(html, "header")
    assert 'class="language-switcher"' in part(html, "header")
    assert '<nav aria-label="Footer">' in part(html, "footer")


def test_404_renders_with_base_and_the_full_chrome(client: Client) -> None:
    response = client.get("/no-such-page/")
    assert response.status_code == 404
    html = response.content.decode()
    assert_base_page(html)
    assert_full_chrome(html)
    assert "layouts/error.html" in [t.name for t in response.templates]
    policy = response["Content-Security-Policy"]
    assert "'unsafe-inline'" not in policy


def test_csrf_failure_renders_with_base(client: Client) -> None:
    client = Client(enforce_csrf_checks=True)
    response = client.post(reverse("logout"), {"next": "/"})
    assert response.status_code == 403
    html = response.content.decode()
    assert_base_page(html)
    assert_full_chrome(html)
    assert "layouts/error.html" in [t.name for t in response.templates]


def test_403_renders_with_base() -> None:
    request = RequestFactory().get("/private/")
    with use_theme(request):
        response = defaults.permission_denied(request, PermissionDenied())
    assert response.status_code == 403
    html = response.content.decode()
    assert_base_page(html)
    assert_full_chrome(html)


def test_500_renders_with_base_and_no_query(
    django_assert_num_queries: DjangoAssertNumQueries,
) -> None:
    request = RequestFactory().get("/broken/")
    with use_theme(request), django_assert_num_queries(0):
        response = views.server_error(request)
    assert response.status_code == 500
    assert_base_page(response.content.decode())


def test_400_renders_with_base_and_no_query(
    django_assert_num_queries: DjangoAssertNumQueries,
) -> None:
    request = RequestFactory().get("/", HTTP_HOST="bad host")
    with use_theme(request), django_assert_num_queries(0):
        response = views.bad_request(request, Exception("bad host"))
    assert response.status_code == 400
    assert_base_page(response.content.decode())


def test_the_footer_shows_the_footer_menu(client: Client) -> None:
    footer = part(client.get("/").content.decode(), "footer")
    nav = re.search(r'<nav aria-label="Footer">(.*?)</nav>', footer, re.DOTALL)
    assert nav is not None
    assert re.search(r"<a href=\"[^\"]+\">[^<]+</a>", nav.group(1))


def test_the_standard_page_uses_the_content_shell(client: Client) -> None:
    response = client.get("/about/")
    assert "layouts/content.html" in [t.name for t in response.templates]
    assert '<div class="page-content">' in response.content.decode()
