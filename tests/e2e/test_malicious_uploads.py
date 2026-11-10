"""E2E (SEC.2): Wagtail's multiple uploader with files from the malicious-upload corpus.

An editor drops a GPS-tagged photo and an HTML page named .jpg on the uploader: the HTML page is
refused on the spot, the photo waits for its licence and author and is then stored without its
EXIF, GPS and XMP, under a name the app made.
"""

import io
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Collection

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.core.models import MediaImage
from apps.core.tests.factories import ImageLicenceFactory
from tests.e2e.login import verify_with_totp
from tests.upload_corpus import HTML_PAGE, SECRET_PLACE, jpeg_with_gps

pytestmark = pytest.mark.django_db(transaction=True)


def test_the_multiple_uploader_refuses_html_and_strips_a_photos_location(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path], tmp_path: Path
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    editor = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=editor)
    ImageLicenceFactory.create(code="own-work", name="Own work", source_required=False)
    if Collection.get_first_root_node() is None:  # flushed by an earlier transactional test
        Collection.add_root(name="Root")
    photo = tmp_path / "holiday.jpg"
    photo.write_bytes(jpeg_with_gps((64, 40)))
    fake = tmp_path / "photo.jpg"
    fake.write_bytes(HTML_PAGE)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))

    page.goto(f"{live_server.url}/admin/images/multiple/add/")
    page.locator("#fileupload").set_input_files([photo, fake])

    uploads = page.locator("#upload-list > li")
    expect(uploads).to_have_count(2)
    refused = uploads.filter(has_text="photo.jpg")
    expect(refused.locator(".error-message")).not_to_be_empty()
    pending = uploads.filter(has_text="holiday.jpg")
    pending.get_by_label("Licence").select_option(label="Own work")
    pending.get_by_label("Author").fill("Ada Lovelace")
    shot("pending-and-refused")
    with page.expect_response(lambda r: "create_from_uploaded_image" in r.url) as answer:
        pending.get_by_role("button", name="Update").click()
    assert answer.value.json()["success"], answer.value.json()
    shot("stored")

    (image,) = MediaImage.objects.all()
    with image.file.open("rb") as handle:
        stored = handle.read()
    assert image.file.name.startswith("original_images/holiday")
    assert SECRET_PLACE not in stored
    with Image.open(io.BytesIO(stored)) as pixels:
        assert not pixels.getexif()
        assert pixels.size == (64, 40)
