"""ARC.32 (part 2) sweep: the help button is on EVERY signed-in account and Manager page.

Part 1 put it on the storefront. This part covers the two unprefixed areas with their own chrome:

- the account area (the layout `layouts/account.html`: sign-in steps, second factor pages), which
  shares the storefront header, and
- the Manager shell (`manager/layout.html`, CORE.17), which has its own header.

For each page the signed-in user of the right role sees a real `<button>` named "Help for this
page" in the header's tools, immediately left of the user menu (whose "Help" item opens the same
dialog), with the page's route name, the static script and the dialog; nothing inline. An anonymous
visitor gets the login redirect as before and no help markup. Manager route names key help entries
(`manager:shop:profile`), and every Manager route name is a valid key.
"""

import re

import pytest
from bs4 import BeautifulSoup
from django.test import Client
from django.urls import resolve, reverse
from django.utils import translation
from django.utils.translation import gettext

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms import engine
from apps.cms.tests.factories import HelpEntryFactory
from apps.compliance.tests.factories import NoticeFactory
from apps.manager.checks import manager_routes
from apps.orders.tests.factories import paid_vendor_order
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

ACCOUNT_ROUTES = ("mfa:overview", "mfa:setup", "mfa:totp")
# The storefront editor's image, document and link choosers answer Wagtail's modal protocol:
# they return modal fragments without the page layout, so there is no help button.
MODAL_FRAGMENTS = (
    "manager:shop:document_choose",
    "manager:shop:document_chosen",
    "manager:shop:document_create",
    "manager:shop:document_results",
    "manager:shop:link_choose",
    "manager:shop:image_choose",
    "manager:shop:image_results",
    "manager:shop:image_create",
    "manager:shop:image_chosen",
    "manager:shop:image_select_format",
)
# Routes that answer a redirect or an action, not a page of the shell.
NOT_PAGES = (
    "manager:shop:order_action",
    "manager:platform:profile_revert",  # POST only, redirects to the changes page
    "manager:platform:profile_confirm",  # POST only, redirects to the changes page
    *MODAL_FRAGMENTS,
)


def _soup(content: bytes) -> BeautifulSoup:
    return BeautifulSoup(content.decode(), "html.parser")


def _assert_help_chrome(response, expected_route: str) -> None:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.request["PATH_INFO"]
    soup = _soup(response.content)
    assert soup.html is not None
    lang = str(soup.html["lang"])
    path = response.request["PATH_INFO"]

    tools = soup.select_one("header .user-tools")
    assert tools is not None, f"{path}: no help chrome in the header"
    button = tools.select_one("button.help-button")
    assert button is not None
    with translation.override(lang):
        assert button["aria-label"] == gettext("Help for this page")
    assert button["type"] == "button"
    assert button.has_attr("data-help-open")
    sibling = button.find_next_sibling()
    assert sibling is not None
    assert "user-menu" in sibling["class"], f"{path}: the button is not left of the user menu"
    assert tools.find_parent(class_="site-header__tools") is not None
    item = sibling.select_one("[data-help-open]")
    assert item is not None
    with translation.override(lang):
        assert item.get_text(strip=True) == gettext("Help")
    assert tools["data-help-url"] == "/_help/entry/"
    assert tools["data-help-lang"] == lang
    assert tools["data-help-route"] == expected_route
    assert soup.select_one("dialog#help-dialog") is not None
    scripts = [str(s.get("src", "")) for s in soup.find_all("script")]
    assert any(src.endswith("themes/base/js/help.js") for src in scripts)
    inline = [s for s in soup.find_all("script") if not s.get("src")]
    data_types = ("application/ld+json", "application/json")  # data (`json_script`), not code
    assert all(s.get("type") in data_types for s in inline)
    assert not [t for t in soup.find_all(True) if t.get("style")]


# --- The account area


@pytest.mark.parametrize("name", ACCOUNT_ROUTES)
def test_a_signed_in_user_has_the_help_button_on_the_account_page(name: str) -> None:
    client = Client()
    client.force_login(UserFactory.create())
    response = client.get(reverse(name), follow=True)
    _assert_help_chrome(response, expected_route=name)


@pytest.mark.parametrize("name", ACCOUNT_ROUTES)
def test_an_anonymous_visitor_is_sent_to_sign_in_with_no_help_markup(name: str) -> None:
    client = Client()
    response = client.get(reverse(name))
    assert response.status_code == 302  # to the sign-in page, as before this part
    assert "login" in response["Location"]
    assert not response.content  # a redirect carries no page, so no help markup


def test_the_account_page_lists_its_fields_from_the_form_markup() -> None:
    """The dialog's "Fields on this page" reads a paragraph's `label[for]` on the hand-written
    second factor forms (`help.js`): the page has one, and its control exists."""
    client = Client()
    client.force_login(UserFactory.create())
    soup = _soup(client.get(reverse("mfa:totp"), follow=True).content)
    labels = soup.select("main form p > label[for]")
    assert labels
    assert all(soup.find(id=str(label["for"])) is not None for label in labels)


# --- The Manager


def _shop_owner() -> tuple[object, object]:
    member = VendorMemberFactory.create(role="owner")
    return member.user, member.vendor


def _manager_pages() -> dict[str, str]:
    """{route name: signed-in role} of every page of the shell the sweep opens."""
    return {
        "manager:shop:dashboard": "shop",
        "manager:shop:profile": "shop",
        "manager:shop:orders": "shop",
        "manager:shop:order": "shop",
        "manager:platform:dashboard": "platform",
        "manager:platform:notices": "platform",
        "manager:platform:notice": "platform",
        "manager:platform:profile_changes": "platform",
        "manager:platform:staff_roles": "platform",
    }


def _open(name: str) -> tuple[Client, str]:
    if _manager_pages()[name] == "shop":
        user, vendor = _shop_owner()
        kwargs = {}
        if name == "manager:shop:order":
            kwargs = {"public_id": paid_vendor_order(vendor).public_id}  # type: ignore[arg-type]
    else:
        user = UserFactory.create(
            permissions=[
                "core.platform_owner",
                "compliance.review_notices",
                "vendors.revert_storefront_profile",
            ]
        )
        kwargs = {}
        if name == "manager:platform:notice":
            kwargs = {"public_id": NoticeFactory.create().public_id}
    client = Client()
    verified_login(client, user)  # type: ignore[arg-type]
    return client, reverse(name, kwargs=kwargs)


@pytest.mark.parametrize("name", list(_manager_pages()))
def test_a_member_with_the_right_role_has_the_help_button_on_the_manager_page(name: str) -> None:
    client, url = _open(name)
    response = client.get(url)
    _assert_help_chrome(response, expected_route=name)
    soup = _soup(response.content)
    assert soup.select_one("header .manager-header__user") is not None  # the shell's own header
    assert soup.select_one("header.manager-header .user-tools") is not None
    assert soup.select_one("button.help-button") is not None


@pytest.mark.parametrize("name", list(_manager_pages()))
def test_an_anonymous_visitor_gets_the_login_redirect_on_the_manager_page(name: str) -> None:
    _client, url = _open(name)
    response = Client().get(url)
    assert response.status_code == 302
    assert "/account/login/" in response["Location"]


def _manager_route_names() -> set[str]:
    """The full URL name (`manager:shop:profile`) of every route under `/manager/`."""
    nil = "00000000-0000-0000-0000-000000000000"
    names = set()
    for route, space in manager_routes():
        assert space is not None
        path = "/" + re.sub(r"<[^>]+>", "0", re.sub(r"<uuid:[^>]+>", nil, route))
        assert resolve(path).view_name != "wagtail_serve", f"{route} fell through to Wagtail"
        names.add(resolve(path).view_name)
    return names


def test_the_sweep_opens_every_manager_page_route() -> None:
    uncovered = _manager_route_names() - set(_manager_pages()) - {"manager:shop:vendor"}
    uncovered -= set(NOT_PAGES)
    assert not uncovered, f"Manager routes the help sweep does not open: {sorted(uncovered)}"


def test_the_vendor_picker_has_the_help_button_for_a_member_of_two_vendors() -> None:
    member = VendorMemberFactory.create(role="owner")
    VendorMemberFactory.create(user=member.user, vendor=VendorFactory.create(), role="staff")
    client = Client()
    verified_login(client, member.user)
    response = client.get(reverse("manager:shop:vendor"))
    _assert_help_chrome(response, expected_route="manager:shop:vendor")


def test_every_manager_route_name_can_key_a_help_entry() -> None:
    for name in _manager_route_names():
        assert engine.help_key_valid(name), name
        assert engine.help_route_name(name) == name


def test_a_help_entry_keyed_by_a_manager_route_answers_the_dialog() -> None:
    HelpEntryFactory.create(
        kind="route",
        key="manager:shop:profile",
        title="Your storefront",
        body="<p>Edit how shoppers see your shop.</p>",
    )
    client, _url = _open("manager:shop:profile")
    response = client.get("/_help/entry/", {"route": "manager:shop:profile", "lang": "en"})
    assert response.status_code == 200
    html = response.content.decode()
    assert "Your storefront" in html
    assert "Edit how shoppers see your shop." in html


def test_the_manager_form_markup_matches_what_the_dialog_lists() -> None:
    """ "Fields on this page" reads `main form .field > label`: the profile form has them, each
    with its help text in `.helptext` where the field has one."""
    client, url = _open("manager:shop:profile")
    soup = _soup(client.get(url).content)
    labels = soup.select("main form .field > label, main form .field__check > label")
    assert len(labels) >= 3
    assert soup.select("main form .field .helptext")


def test_the_manager_header_is_not_floating_and_is_script_free() -> None:
    client, url = _open("manager:shop:dashboard")
    html = client.get(url).content.decode()
    assert 'style="' not in html
    assert "onclick=" not in html
