"""THM.3 done-when: home, standard page and login render with `base` (spec 14 §14.3, §14.6).

The CMS.1 home and standard page templates and the sign-in (MFA verification) page now extend
the `base` theme's layouts: the built `theme.css` is linked, the skip link is the first focusable
element, the header carries the disclosure navigation, the language switcher and the help link,
and the help link sits in the same place in the header and the footer on every page.
"""

import json
import re

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import password_login
from apps.cms.services import seed_site

pytestmark = pytest.mark.django_db

HELP_URL = "/help/"
FOCUSABLE = re.compile(r"<(a|button|input|select|textarea)\b[^>]*>")


@pytest.fixture(autouse=True)
def site(settings: SettingsWrapper) -> None:
    settings.MPMV_HELP_URL = HELP_URL
    settings.MPMV_THEME_DEFAULT = "base"
    seed_site()


def render(client: Client, path: str) -> tuple[str, list[str]]:
    response = client.get(path)
    assert response.status_code == 200
    policy = response["Content-Security-Policy"]
    assert "'unsafe-inline'" not in policy
    assert "'unsafe-eval'" not in policy
    return response.content.decode(), [t.name for t in response.templates if t.name]


def login_page(client: Client) -> tuple[str, list[str]]:
    user = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=user)
    password_login(client, user)
    return render(client, reverse("mfa:verify"))


@pytest.fixture(params=["home", "standard", "login"])
def page(request: pytest.FixtureRequest, client: Client) -> tuple[str, list[str]]:
    if request.param == "login":
        return login_page(client)
    return render(client, {"home": "/", "standard": "/about/"}[request.param])


def body(html: str) -> str:
    return html.split("<body", 1)[1]


def test_the_page_extends_the_base_layout(page: tuple[str, list[str]]) -> None:
    html, templates = page
    assert "layouts/base.html" in templates
    assert "cms/base.html" not in templates
    assert "core/base.html" not in templates
    assert 'data-theme="base"' in html


def test_cms_pages_use_the_public_layout_and_login_the_account_layout(client: Client) -> None:
    assert "layouts/account.html" not in render(client, "/")[1]
    assert "layouts/account.html" in login_page(client)[1]


def test_the_built_theme_css_is_linked(page: tuple[str, list[str]]) -> None:
    html, _ = page
    assert re.search(r'<link rel="stylesheet" href="/static/themes/base/theme\.css"', html)
    assert "cms/site.css" not in html


def test_the_skip_link_is_the_first_focusable_element(page: tuple[str, list[str]]) -> None:
    html, _ = page
    first = FOCUSABLE.search(body(html))
    assert first is not None
    assert 'class="skip-link"' in first.group(0)
    assert 'href="#main"' in first.group(0)
    assert len(re.findall(r"<main\b", html)) == 1
    assert re.search(r'<main id="main"[^>]*tabindex="-1"', html)


def test_one_h1_per_page(page: tuple[str, list[str]]) -> None:
    html, _ = page
    assert len(re.findall(r"<h1\b", html)) == 1


def test_the_header_has_a_labelled_disclosure_navigation(page: tuple[str, list[str]]) -> None:
    html, _ = page
    header = html.split('<header class="site-header">', 1)[1].split("</header>", 1)[0]
    nav = re.search(r'<nav id="site-nav"[^>]*aria-label="Main"', header)
    assert nav is not None
    button = re.search(r"<button[^>]*aria-controls=\"site-nav\"[^>]*>", header)
    assert button is not None
    # Without JavaScript the list is simply shown and the menu button stays hidden.
    assert 'aria-expanded="false"' in button.group(0)
    assert " hidden" in button.group(0)
    assert 'x-data="disclosure"' in header


def test_the_header_has_the_language_switcher(page: tuple[str, list[str]]) -> None:
    html, _ = page
    header = html.split('<header class="site-header">', 1)[1].split("</header>", 1)[0]
    assert re.search(r'<nav class="language-switcher" aria-label="Language"', header)
    for code in ("en", "it"):
        assert f'name="language" value="{code}"' in header


def test_the_help_link_sits_in_the_header_and_the_footer(page: tuple[str, list[str]]) -> None:
    html, _ = page
    header = html.split('<header class="site-header">', 1)[1].split("</header>", 1)[0]
    footer = html.split('<footer class="site-footer">', 1)[1].split("</footer>", 1)[0]
    for part in (header, footer):
        assert re.search(rf'<a class="help-link" href="{HELP_URL}">', part)


def test_without_a_help_url_no_empty_help_link_is_rendered(
    client: Client, settings: SettingsWrapper
) -> None:
    settings.MPMV_HELP_URL = ""
    html, _ = render(client, "/")
    assert 'class="help-link"' not in html


def test_htmx_is_configured_for_the_strict_csp(page: tuple[str, list[str]]) -> None:
    html, _ = page
    meta = re.search(r"<meta name=\"htmx-config\" content='([^']*)'>", html)
    assert meta is not None
    config = json.loads(meta.group(1))
    assert config["allowEval"] is False
    assert config["allowScriptTags"] is False
    assert config["includeIndicatorStyles"] is False
    assert config["selfRequestsOnly"] is True


def test_only_same_origin_script_files_are_loaded(page: tuple[str, list[str]]) -> None:
    html, _ = page
    scripts = re.findall(r"<script\b([^>]*)>", html)
    sources = [re.search(r'src="([^"]+)"', attrs) for attrs in scripts]
    assert all(source is not None for source in sources), "inline <script> found"
    urls = [source.group(1) for source in sources if source is not None]
    assert all(url.startswith("/static/") for url in urls), urls
    assert "/static/vendor/htmx.min.js" in urls
    assert "/static/vendor/alpine-csp.min.js" in urls
    assert "/static/themes/base/js/base.js" in urls
    # The theme module registers its Alpine components before Alpine starts.
    assert urls.index("/static/themes/base/js/base.js") < urls.index(
        "/static/vendor/alpine-csp.min.js"
    )
    assert "<style" not in html
    assert not re.search(r"\sstyle=", html)


def test_the_login_switcher_returns_to_the_same_page(client: Client) -> None:
    html, _ = login_page(client)
    assert f'name="next" value="{reverse("mfa:verify")}"' in html
