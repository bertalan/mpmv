"""E2E (CMS.8): an image upload in the Wagtail admin needs its licence and author.

Done when: "An upload without licence or author is refused". An editor uploads an image with
no licence and no author and gets the errors; with a free licence, its author and source the
image is saved.
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Collection

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.models import MediaImage
from apps.core.tests.factories import ImageLicenceFactory, MediaImageFactory
from tests.e2e.login import verify_with_totp
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _axe_helper_style(violation: str) -> bool:
    """Known since CMS.1: Wagtail's accessibility checker adds a <style> the CSP blocks."""
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation


def test_an_upload_without_licence_or_author_is_refused_then_accepted_with_them(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path], tmp_path: Path
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    editor = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=editor)
    # Transactional tests flush the seeded allow list: put back the entry this test picks.
    ImageLicenceFactory.create(
        code="CC-BY-4.0",
        name="CC BY 4.0",
        url="https://creativecommons.org/licenses/by/4.0/",
        attribution_required=True,
    )
    if Collection.get_first_root_node() is None:  # flushed by an earlier transactional test
        Collection.add_root(name="Root")
    upload = tmp_path / "lake.png"
    upload.write_bytes(
        MediaImageFactory.build(file__filename="lake.png", file__format="PNG").file.read()
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))

    page.goto(f"{live_server.url}/admin/images/add/")
    page.get_by_label("Title").fill("Lake at dawn")
    page.locator("input[type=file][name=file]").set_input_files(upload)
    page.locator("form [type=submit]").last.click()

    expect(page.get_by_text("Choose the image's licence.")).to_be_visible()
    expect(page.get_by_text("Name the author or rights holder to credit.")).to_be_visible()
    assert not MediaImage.objects.filter(title="Lake at dawn").exists()
    shot("upload-refused")

    page.locator("input[type=file][name=file]").set_input_files(upload)
    page.get_by_label("Licence").select_option(label="CC BY 4.0")
    page.get_by_label("Author").fill("Ada Lovelace")
    page.get_by_label("Source URL").fill("https://example.com/lake")
    page.locator("form [type=submit]").last.click()

    expect(page).not_to_have_url(re.compile(r"/images/add/"))
    expect(page.locator(".messages")).to_be_visible()
    image = MediaImage.objects.get(title="Lake at dawn")
    assert image.author == "Ada Lovelace"
    assert image.licence is not None
    assert image.licence.code == "CC-BY-4.0"
    shot("upload-accepted")

    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)
