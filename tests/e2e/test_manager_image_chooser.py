"""E2E (VEN.31 part 1): the owner inserts an image into the storefront text from the Manager
chooser, in a real browser, without Wagtail admin access. Screenshots are kept for review."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Collection

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.tests.factories import ImageLicenceFactory, MediaImageFactory
from apps.vendors import engine
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.login import verify_with_totp
from tests.upload_corpus import png

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_owner_chooses_and_uploads_an_image_for_the_storefront_text(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)
    owner = UserFactory.create(email="owner@example.com", password=password)
    device = TOTPDeviceFactory.create(user=owner)
    shop = VendorFactory.create(display_name="Shop West", status="active")
    other = VendorFactory.create(display_name="Shop East", status="active")
    VendorMemberFactory.create(vendor=shop, user=owner, role="owner")
    ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    root = Collection.get_first_root_node() or Collection.add_root(name="Root")
    mine = root.add_child(name=engine.vendor_collection_name(str(shop.public_id)))
    theirs = root.add_child(name=engine.vendor_collection_name(str(other.public_id)))
    MediaImageFactory.create(collection=mine, title="Our anvil")
    MediaImageFactory.create(collection=theirs, title="Their secret plan")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/account/login/")
    page.get_by_label("Email").fill("owner@example.com")
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))
    page.goto(f"{live_server.url}/manager/shop/profile/")

    editor = page.locator(".Draftail-Editor").first
    editor.locator(".public-DraftEditor-content").click()
    page.get_by_role("button", name="Insert a block").first.click()
    page.get_by_role("option", name="Image", exact=True).click()
    dialog = page.locator(".modal-body")
    expect(dialog.get_by_text("Our anvil").first).to_be_visible()
    expect(dialog.get_by_text("Their secret plan")).to_have_count(0)
    shot("chooser-list")

    dialog.get_by_role("tab", name="Upload").click()
    dialog.locator("input[type=file]").set_input_files(
        {"name": "lake.png", "mimeType": "image/png", "buffer": png((64, 48))}
    )
    dialog.get_by_label("Title").fill("Lake view")
    shot("chooser-upload")
    dialog.get_by_role("button", name="Upload").click()
    expect(dialog.get_by_role("button", name="Insert image")).to_be_visible()
    shot("chooser-format")
    dialog.get_by_label("Full width").check()
    dialog.get_by_label("Alt text").fill("A lake at dawn")
    dialog.get_by_role("button", name="Insert image").click()
    expect(editor.locator("img")).to_be_visible()
    shot("image-inserted")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Storefront profile saved.")).to_be_visible()
    # Draftail's own style elements are the known CSP noise (see test_manager_shell); the
    # chooser itself adds no violation.
    noise = "style-src-elem blocked inline"
    assert [v for v in csp_violations(page) if noise not in v] == []
