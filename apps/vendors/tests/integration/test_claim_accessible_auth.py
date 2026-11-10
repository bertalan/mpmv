"""CMP.9 (WCAG 2.2 SC 3.3.8): the vendor claim's sign-in steps (VEN.24) are accessible.

Password managers fill the password and code fields, and every password field gets the same
show-password toggle as the staff and editors' logins, loaded as a static module.
"""

from html.parser import HTMLParser

import pytest
from django.test import Client

from apps.accounts.tests.factories import UserFactory
from apps.vendors.tests.integration.test_claim import (
    confirm_identity,
    imported_vendor,
    open_link,
    send_link,
)

pytestmark = pytest.mark.django_db

TOGGLE_SCRIPT = "accounts/password-toggle.js"


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {name: value or "" for name, value in attrs}))


def _assert_accessible(html: bytes, passwords: set[str], token: str) -> None:
    parser = _Tags()
    parser.feed(html.decode())
    inputs = {a["name"]: a for t, a in parser.tags if t == "input" and a.get("name")}
    for name in passwords:
        assert inputs[name]["type"] == "password"
        assert inputs[name]["autocomplete"] == token
        assert inputs[name].get("data-password-toggle")
    assert inputs["code"]["autocomplete"] == "one-time-code"
    scripts = [a for t, a in parser.tags if t == "script" and TOGGLE_SCRIPT in a.get("src", "")]
    assert scripts
    assert scripts[0].get("type") == "module"


def test_the_new_account_step_offers_the_password_toggle(client: Client) -> None:
    vendor = imported_vendor()
    open_link(client, send_link(vendor))
    response = confirm_identity(client, vendor, vendor.vat_number)
    _assert_accessible(response.content, {"password1", "password2"}, "new-password")


def test_the_sign_in_step_offers_the_password_toggle(client: Client) -> None:
    vendor = imported_vendor()
    UserFactory.create(email=vendor.contact_email)
    open_link(client, send_link(vendor))
    response = confirm_identity(client, vendor, vendor.vat_number)
    _assert_accessible(response.content, {"password"}, "current-password")
