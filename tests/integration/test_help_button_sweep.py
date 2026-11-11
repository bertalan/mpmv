"""ARC.32 (part 1) sweep: the help button is on EVERY signed-in storefront page and on no other.

For each storefront route class of the language-switcher sweep (CMS pages, legal pages, the blog,
events, landing pages, listings, vendor storefronts and the 404 page) the test renders the page
as a signed-in user and as a visitor:

- signed in: a real `<button>` named "Help for this page" sits in the header's tools, immediately
  left of the user menu, with the route name and page type the modal asks the server for; the
  user menu has a "Help" item; the dialog and the static script are there; no inline script or
  style comes with them;
- anonymous: no help markup and no help script (the chrome never reads a visitor's session). A
  cached public page therefore carries nothing of it (the signed-in chrome is never cached).

Account, Manager and the admins come with the later parts of ARC.32.
"""

import pytest
from bs4 import BeautifulSoup
from django.test import Client
from django.utils import translation
from django.utils.translation import gettext

from apps.accounts.tests.factories import UserFactory
from tests.integration.test_language_switcher_everywhere import CASES

pytestmark = pytest.mark.django_db

STOREFRONT_PREFIXES = (
    "cms-",
    "legal-",
    "blog-",
    "event-",
    "vendor-landing",
    "sell-with-us-cms",
    "term-",
    "brand-",
    "category-",
    "vendor-storefront",
    "error-404",
)
NAMES = [name for name in CASES if name.startswith(STOREFRONT_PREFIXES)]
PAGE_TYPE_CLASSES = ("cms-", "legal-", "blog-", "event-", "vendor-landing", "sell-with-us-cms")
HELP_MARKERS = ("data-help", "help-dialog", "help.js", "user-tools", "user-menu")


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _soup(content: bytes) -> BeautifulSoup:
    return BeautifulSoup(content.decode(), "html.parser")


def test_the_sweep_covers_every_storefront_route_class() -> None:
    assert len(NAMES) >= 22


@pytest.mark.parametrize("name", NAMES)
def test_a_signed_in_user_has_the_help_button_on_the_page(name: str) -> None:
    builder, _marks = CASES[name]
    case = builder()
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(case.url, follow=True)
    assert response.status_code == case.status
    soup = _soup(response.content)
    assert soup.html is not None
    lang = str(soup.html["lang"])

    tools = soup.select_one("header .user-tools")
    assert tools is not None, f"{case.url}: no help chrome in the header"
    button = tools.select_one("button.help-button")
    assert button is not None
    with translation.override(lang):
        assert button["aria-label"] == gettext("Help for this page")
    assert button.has_attr("data-help-open")
    assert button["aria-haspopup"] == "dialog"
    assert button["type"] == "button"
    # Immediately left of the user menu, in the header's tools (right side).
    sibling = button.find_next_sibling()
    assert sibling is not None
    assert "user-menu" in sibling["class"]
    assert tools.find_parent(class_="site-header__tools") is not None
    # The menu's own item opens the same modal.
    item = sibling.select_one("[data-help-open]")
    assert item is not None
    with translation.override(lang):
        assert item.get_text(strip=True) == gettext("Help")
    # What the modal asks the server for.
    assert tools["data-help-url"] == "/_help/entry/"
    assert tools["data-help-lang"] == lang
    assert "data-help-route" in tools.attrs
    assert "data-help-page-type" in tools.attrs
    if name.startswith(PAGE_TYPE_CLASSES):
        assert tools["data-help-page-type"], f"{case.url}: no page type"
    elif not name.startswith("error-"):  # an error page names no screen: the general entry
        assert tools["data-help-route"], f"{case.url}: no route name"
    # The dialog and the static script; nothing inline.
    dialog = soup.select_one("dialog#help-dialog")
    assert dialog is not None
    assert dialog.get("aria-labelledby") == "help-dialog-title"
    scripts = [str(s.get("src", "")) for s in soup.find_all("script")]
    assert any(src.endswith("themes/base/js/help.js") for src in scripts)
    inline = [s for s in soup.find_all("script") if not s.get("src")]
    assert all(s.get("type") == "application/ld+json" for s in inline)  # data, not code
    assert not [t for t in soup.find_all(True) if t.get("style")]
    assert "private" in response.get("Cache-Control", "")  # the signed-in chrome is never cached


@pytest.mark.parametrize("name", NAMES)
def test_an_anonymous_visitor_has_no_help_markup_on_the_page(name: str) -> None:
    builder, _marks = CASES[name]
    case = builder()
    response = Client().get(case.url, follow=True)
    assert response.status_code == case.status
    html = response.content.decode()
    for marker in HELP_MARKERS:
        assert marker not in html, f"{case.url}: {marker!r} on a public page"


def test_the_help_chrome_follows_the_pages_language() -> None:
    builder, _marks = CASES["cms-home-it"]
    case = builder()
    client = Client()
    client.force_login(UserFactory.create())
    soup = _soup(client.get(case.url).content)
    tools = soup.select_one("header .user-tools")
    assert tools is not None
    assert tools["data-help-lang"] == "it"
    button = tools.select_one("button.help-button")
    assert button is not None
    assert button["aria-label"] == "Aiuto per questa pagina"
