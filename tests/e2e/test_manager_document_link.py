"""E2E (VEN.31 part 2): the owner links an external address from the Manager editor, in a real
browser, without Wagtail admin access; the editor has no document link (ARC.15). Screenshots are
kept for review."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Collection

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.vendors import engine
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_owner_links_an_external_address_and_has_no_document_link(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    owner = UserFactory.create(email="owner@example.com", password=password)
    device = TOTPDeviceFactory.create(user=owner)
    shop = VendorFactory.create(display_name="Shop West", status="active")
    other = VendorFactory.create(display_name="Shop East", status="active")
    VendorMemberFactory.create(vendor=shop, user=owner, role="owner")
    root = Collection.get_first_root_node() or Collection.add_root(name="Root")
    root.add_child(name=engine.vendor_collection_name(str(shop.public_id)))
    root.add_child(name=engine.vendor_collection_name(str(other.public_id)))
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/account/login/")
    page.get_by_label("Email").fill("owner@example.com")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/manager/shop/profile/")

    editor = page.locator(".Draftail-Editor").first
    content = editor.locator(".public-DraftEditor-content")
    content.click()
    # No document link until private storage (ARC.15, owner decision).
    expect(page.get_by_role("button", name="Document")).to_have_count(0)
    content.press_sequentially("Read our site")
    content.press("Shift+Control+ArrowLeft")
    content.press("Shift+Control+ArrowLeft")
    page.get_by_role("button", name="Link").first.click()
    dialog = page.locator(".modal-body")
    dialog.get_by_label("Web address").fill("javascript:alert(1)")
    dialog.get_by_role("button", name="Insert link").click()
    expect(dialog.get_by_text("https://")).to_be_visible()
    shot("link-refused")
    dialog.get_by_label("Web address").fill("https://example.com/")
    dialog.get_by_role("button", name="Insert link").click()
    expect(editor.locator("a")).to_have_count(1)
    shot("link-inserted")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Storefront profile saved.")).to_be_visible()
    noise = "style-src-elem blocked inline"
    assert [v for v in csp_violations(page) if noise not in v] == []
