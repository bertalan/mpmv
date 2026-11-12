"""ARC.32 (parts 3 and 4) sweep: the help button is on EVERY screen of both admins.

The sweep walks the URL map of the editors' admin (`/admin/`, Wagtail) and of the staff admin
(`/django-admin/`): every route without a URL argument is requested by a signed-in, verified
superuser, and every full HTML screen that answers must carry the help button.

- Wagtail 8.1 has no header and no hook for the account menu (it sits in the sidebar's footer, drawn
  by Wagtail's own script), so the closest place is the sidebar's "Help" submenu: an item "Help for
  this page" next to "Shortcuts", carrying the route name, page type and language the dialog asks
  for. The static script and style come in through `insert_global_admin_js` and
  `insert_global_admin_css`; nothing is inline.
- The staff admin's header has a real `<button>` "Help for this page" immediately left of the user
  tools (`{% block usertools %}` of `admin/base_site.html`).
- The admin allow-list (OPS.5) still applies: from a network that is not allowed the staff admin is
  the normal 404, with no help markup in it.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.contrib import admin
from django.contrib.auth.models import Group
from django.test import Client, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, resolve, reverse
from django.utils import translation
from django.utils.translation import gettext
from wagtail.models import Page

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms import roles
from apps.cms.models import StandardPage
from apps.cms.services import seed_site

pytestmark = pytest.mark.django_db

SCRIPT = "cms/js/admin-help.js"
STYLE = "cms/css/admin-help.css"
# Routes that sign out, sign in or change something on a GET are not requested.
SKIPPED = re.compile(
    r"login|logout|password_reset|unlock|bulk|sprite|jsi18n|failwhale|autocomplete|userbar"
)
ARGUMENT = re.compile(r"[<(\[^$]")


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _routes(
    patterns: list[Any], prefix: str = "", namespaces: tuple[str, ...] = ()
) -> Iterator[tuple[str, str]]:
    for entry in patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            inner = (*namespaces, entry.namespace) if entry.namespace else namespaces
            yield from _routes(entry.url_patterns, route, inner)
        elif isinstance(entry, URLPattern) and entry.name:
            yield "/" + route, ":".join((*namespaces, entry.name))


def static_routes(root: str) -> list[tuple[str, str]]:
    """(path, view name) of every route under `root` that takes no argument, once per path."""
    found: dict[str, str] = {}
    for path, name in _routes(get_resolver().url_patterns):
        if path.startswith(root) and not ARGUMENT.search(path) and not SKIPPED.search(path):
            found.setdefault(path, name)
    return sorted((path, name) for path, name in found.items())


def _soup(content: bytes) -> BeautifulSoup:
    return BeautifulSoup(content.decode(), "html.parser")


def _lang(soup: BeautifulSoup) -> str:
    return str(soup.html["lang"]) if soup.html is not None and soup.html.has_attr("lang") else "en"


def _help_attrs(node: Any) -> dict[str, str] | None:
    """The attributes of Wagtail's "Help for this page" menu item, found in the sidebar props."""
    if isinstance(node, dict):
        if "data-admin-help-open" in node:
            return {str(k): str(v) for k, v in node.items()}
        for value in node.values():
            if (found := _help_attrs(value)) is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            if (found := _help_attrs(value)) is not None:
                return found
    return None


def wagtail_help(soup: BeautifulSoup) -> dict[str, str] | None:
    props = soup.select_one("script#wagtail-sidebar-props")
    assert props is not None
    return _help_attrs(json.loads(props.get_text()))


def _superuser_client() -> Client:
    client = Client()
    verified_login(client, UserFactory.create(superuser=True))
    return client


def test_the_sweep_covers_both_admins() -> None:
    assert len(static_routes("/admin/")) >= 25
    assert len(static_routes("/django-admin/")) >= 15


def test_every_wagtail_screen_has_the_help_item_script_and_style() -> None:
    client = _superuser_client()
    seen: list[str] = []
    for path, _name in static_routes("/admin/"):
        response = client.get(path)
        if response.status_code != 200 or "text/html" not in response.get("Content-Type", ""):
            continue
        soup = _soup(response.content)
        if soup.select_one("script#wagtail-sidebar-props") is None:
            continue  # a fragment or a page without the sidebar
        seen.append(path)
        attrs = wagtail_help(soup)
        assert attrs is not None, f"{path}: no help item in the sidebar"
        assert attrs["data-help-url"] == "/_help/entry/"
        assert attrs["data-help-route"] == resolve(path).view_name, path
        assert attrs["data-help-lang"] == _lang(soup)
        sources = [str(s.get("src", "")) for s in soup.find_all("script")]
        assert any(src.endswith(SCRIPT) for src in sources), f"{path}: no help script"
        sheets = [str(link.get("href", "")) for link in soup.find_all("link")]
        assert any(href.endswith(STYLE) for href in sheets), f"{path}: no help style"
    assert len(seen) >= 12, seen


def test_every_staff_admin_screen_has_the_help_button_left_of_the_user_tools() -> None:
    client = _superuser_client()
    paths = {path for path, _name in static_routes("/django-admin/")}
    for model in admin.site._registry:
        meta = model._meta
        paths.add(reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist"))
        paths.add(reverse(f"admin:{meta.app_label}_{meta.model_name}_add"))
    seen: list[str] = []
    for path in sorted(paths):
        response = client.get(path)
        if response.status_code != 200 or "text/html" not in response.get("Content-Type", ""):
            continue
        soup = _soup(response.content)
        tools = soup.select_one("#user-tools")
        if tools is None:
            continue
        seen.append(path)
        button = tools.find_previous_sibling("button")
        assert button is not None, f"{path}: no help button left of the user tools"
        assert button.get("type") == "button"
        assert "help-button" in button["class"]
        assert button.has_attr("data-admin-help-open")
        with translation.override(_lang(soup)):
            assert button["aria-label"] == gettext("Help for this page")
        assert button["data-help-url"] == "/_help/entry/"
        assert button["data-help-route"] == resolve(path).view_name, path
        assert button["data-help-page-type"] == ""
        sources = [str(s.get("src", "")) for s in soup.find_all("script")]
        assert any(src.endswith(SCRIPT) for src in sources), f"{path}: no help script"
        sheets = [str(link.get("href", "")) for link in soup.find_all("link")]
        assert any(href.endswith(STYLE) for href in sheets), f"{path}: no help style"
        inline = [s for s in soup.find_all("script") if not s.get("src")]
        assert all(s.get("nonce") or s.get("type") == "application/json" for s in inline)
    assert len(seen) >= 40, len(seen)


def test_the_page_type_of_the_page_being_edited_keys_the_wagtail_help() -> None:
    seed_site()
    home = Page.objects.get(depth=2, locale__language_code="en")
    client = _superuser_client()
    page = home.add_child(
        instance=StandardPage(title="About", slug="about-help", locale=home.locale)
    )
    edit = client.get(reverse("wagtailadmin_pages:edit", args=[page.pk]))
    attrs = wagtail_help(_soup(edit.content))
    assert attrs is not None
    assert attrs["data-help-route"] == "wagtailadmin_pages:edit"
    assert attrs["data-help-page-type"] == "cms.StandardPage"
    add = client.get(reverse("wagtailadmin_pages:add", args=("cms", "standardpage", home.pk)))
    attrs = wagtail_help(_soup(add.content))
    assert attrs is not None
    assert attrs["data-help-page-type"] == "cms.StandardPage"
    listing = client.get(reverse("wagtailadmin_explore", args=[home.pk]))
    attrs = wagtail_help(_soup(listing.content))
    assert attrs is not None
    assert attrs["data-help-page-type"] == ""


def test_a_page_editor_without_staff_status_has_the_help_item() -> None:
    user = UserFactory.create()
    user.groups.add(Group.objects.get(name=roles.PAGE_EDITORS))
    client = Client()
    verified_login(client, user)
    response = client.get("/admin/")
    assert response.status_code == 200
    assert wagtail_help(_soup(response.content)) is not None


def test_the_wagtail_login_page_has_no_help() -> None:
    html = Client().get("/admin/login/").content.decode()
    assert "data-admin-help-open" not in html
    assert SCRIPT not in html  # the global hooks' tags are dropped for a visitor
    assert STYLE not in html
    assert "core.css" in html  # only ours: Wagtail's own assets stay


def test_a_signed_in_wagtail_page_still_loads_the_help_assets() -> None:
    html = _superuser_client().get("/admin/").content.decode()
    assert SCRIPT in html
    assert STYLE in html


def test_without_the_help_endpoint_the_wagtail_help_item_is_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from django.urls import NoReverseMatch

    def missing(*args: Any, **kwargs: Any) -> str:
        raise NoReverseMatch("help_entry")

    client = _superuser_client()
    monkeypatch.setattr("apps.cms.help_admin.reverse", missing)
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "data-admin-help-open" not in response.content.decode()
    assert '"help-for-this-page"' not in response.content.decode()


def test_the_help_dialog_endpoint_answers_a_verified_staff_member_for_admin_routes() -> None:
    client = _superuser_client()
    response = client.get("/_help/entry/", {"route": "admin:index", "page_type": "", "lang": "en"})
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]


def test_a_visitor_gets_no_help_in_the_staff_admin_login() -> None:
    html = Client().get("/django-admin/login/").content.decode()
    assert "data-admin-help-open" not in html
    assert SCRIPT not in html


@override_settings(ADMIN_ALLOWED_NETWORKS=["192.0.2.0/24"])
def test_the_staff_admin_allow_list_still_answers_404_from_outside() -> None:
    client = Client()
    verified_login(client, UserFactory.create(superuser=True))
    for path in ("/django-admin/", "/django-admin/accounts/user/", "/django-admin/login/"):
        response = client.get(path, REMOTE_ADDR="203.0.113.9")
        assert response.status_code == 404
        html = response.content.decode()
        assert "data-admin-help-open" not in html
        assert SCRIPT not in html
    allowed = client.get("/django-admin/", REMOTE_ADDR="192.0.2.50")
    assert allowed.status_code == 200
    assert "data-admin-help-open" in allowed.content.decode()


def test_the_help_script_is_served_as_a_static_file_with_no_inline_code() -> None:
    from django.contrib.staticfiles import finders

    script = finders.find(SCRIPT)
    style = finders.find(STYLE)
    assert script
    assert style
    text = Path(script).read_text(encoding="utf-8")
    assert not re.search(r"\.(inner|outer)HTML\s*=|insertAdjacentHTML", text)  # parsed nodes only
    assert "eval(" not in text
    assert "AbortController" in text  # a stale answer never replaces a newer one
    assert 'event.key === " "' in text  # a role=button link answers Space as well as Enter
