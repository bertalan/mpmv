"""VEN.31 part 2 (D-34): the Manager-native link dialog (external links only).

The `link` feature returns to the storefront editor with a dialog served in the Manager instead of
Wagtail's page chooser: https addresses only; internal page links stay refused on save.
"""

import json
from typing import Any

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.cms.engine import EXTERNAL_LINK_REL, sanitise_rich_text
from apps.manager.forms import MANAGER_RICH_TEXT_FEATURES, StorefrontProfileForm
from apps.vendors.models import Vendor
from apps.vendors.services import media_problem
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import SHOP

pytestmark = pytest.mark.django_db

LINK = f"{SHOP}media/links/"
FIELD = "external-link-chooser-url"


def _json(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.status_code
    return json.loads(response.content)  # type: ignore[no-any-return]


def test_the_dialog_opens_for_the_owner(client: Client, owner: User) -> None:
    verified_login(client, owner)
    data = _json(client.get(LINK))
    assert data["step"] == "external_link"
    assert FIELD in data["html"]


def test_an_https_address_is_chosen(client: Client, owner: User) -> None:
    verified_login(client, owner)
    data = _json(
        client.post(
            LINK, {FIELD: "https://example.com/a?b=1#c", "external-link-chooser-link_text": "Site"}
        )
    )
    assert data["step"] == "external_link_chosen"
    assert data["result"]["url"] == "https://example.com/a?b=1#c"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:x",
        "mailto:a@example.com",
        "tel:+390000",
        "//example.com",
        "/shop/other/",
        "#top",
        "https://",
        "https:///path",
        "https://user:pw@example.com/",
        "https://exa mple.com",
        "https://example.com/\nX",
        "https://example.com/" + "a" * 2100,
        "",
    ],
)
def test_anything_but_a_plain_https_address_is_refused(
    client: Client, owner: User, url: str
) -> None:
    verified_login(client, owner)
    data = _json(client.post(LINK, {FIELD: url}))
    assert data["step"] != "external_link_chosen"


def test_staff_and_anonymous_are_refused(client: Client, vendor: Vendor, customer: User) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    assert client.get(LINK).status_code == 302
    for user in (staff, customer):
        verified_login(client, user)
        assert client.get(LINK).status_code in (403, 404)
        assert client.post(LINK, {FIELD: "https://example.com"}).status_code in (403, 404)
        client.logout()


def test_the_editor_has_the_link_feature_with_the_manager_dialog(
    client: Client, owner: User
) -> None:
    assert "link" in MANAGER_RICH_TEXT_FEATURES
    assert "embed" not in MANAGER_RICH_TEXT_FEATURES
    widget = StorefrontProfileForm().fields["description"].widget
    urls = [e["chooserUrls"] for e in widget.options["entityTypes"] if e["type"] == "LINK"]
    assert len(urls) == 1
    for name in ("pageChooser", "externalLinkChooser"):
        assert str(urls[0][name]) == LINK
    for name in ("emailLinkChooser", "phoneLinkChooser", "anchorLinkChooser"):
        assert not urls[0].get(name)
    verified_login(client, owner)
    page = client.get(f"{SHOP}profile/").content.decode()
    assert "/admin/choose-page" not in page
    assert LINK in page


def test_an_external_link_is_kept_with_a_safe_rel_and_a_script_link_is_dropped() -> None:
    clean = sanitise_rich_text('<p><a href="https://example.com/x">ok</a></p>', None)
    assert 'href="https://example.com/x"' in clean
    assert f'rel="{EXTERNAL_LINK_REL}"' in clean
    for bad in ("javascript:alert(1)", "data:text/html,x", "vbscript:x"):
        assert bad not in sanitise_rich_text(f'<p><a href="{bad}">x</a></p>', None)


def test_internal_page_links_stay_refused(vendor: Vendor) -> None:
    assert media_problem(vendor, '<p><a linktype="page" id="1">p</a></p>') is not None
    assert media_problem(vendor, '<p><a href="https://example.com">p</a></p>') is None


@pytest.mark.parametrize(
    "href",
    [
        "http://example.com",
        "//evil.example",
        "mailto:a@example.com",
        "tel:+390000",
        "ftp://example.com/x",
        "/relative",
        "#top",
        "https://user:pw@example.com/",
        " https://example.com",
        "\thttps://example.com",
        "https:/example.com",
        "https://",
    ],
)
def test_the_save_refuses_a_link_that_is_not_https_whatever_the_dialog_did(
    vendor: Vendor, href: str
) -> None:
    """The https-only policy is server-side: a crafted POST cannot skip the dialog."""
    clean = sanitise_rich_text(f'<p><a href="{href}">x</a></p>', None)
    if "<a" in clean:  # the sanitiser dropped the link or kept it: either way the save decides
        assert media_problem(vendor, clean) is not None
    assert media_problem(vendor, f'<p><a href="{href}">x</a></p>') is not None


def test_https_links_and_document_links_pass_the_link_policy(vendor: Vendor) -> None:
    assert media_problem(vendor, '<p><a href="https://example.com/a?b=1&amp;c=2">x</a></p>') is None
    assert media_problem(vendor, "<p>no link</p>") is None


def test_an_external_link_asks_search_engines_not_to_follow_it() -> None:
    clean = sanitise_rich_text('<p><a href="https://example.com/x">ok</a></p>', None)
    assert "nofollow" in clean
    assert "ugc" in clean
