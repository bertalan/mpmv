"""CORE.9 part 2b: the logo form on the storefront profile page (owner only)."""

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog, MediaImage
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory
from tests.upload_corpus import png

from .conftest import SHOP

pytestmark = pytest.mark.django_db

PROFILE = f"{SHOP}profile/"


def _post_logo(client: Client, **fields: object) -> HttpResponse:
    data = {"action": "logo", "language": "en", **fields}
    return client.post(PROFILE, data)


def _upload(name: str = "logo.png", data: bytes | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, png() if data is None else data)


def test_the_page_has_a_logo_form_with_a_file_input_and_alt_field(
    client: Client, owner: User
) -> None:
    verified_login(client, owner)
    page = client.get(PROFILE).text
    assert 'enctype="multipart/form-data"' in page
    assert 'type="file"' in page
    assert 'name="logo_alt"' in page
    assert 'for="id_logo_alt"' in page
    assert 'for="id_logo"' in page


def test_the_owner_uploads_a_logo_and_the_page_shows_it_with_alt_text(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    response = _post_logo(client, logo=_upload(), logo_alt="Shop One mark")
    assert response.status_code == 302
    vendor.refresh_from_db()
    assert vendor.logo is not None
    page = client.get(PROFILE).text
    match = re.search(r'<img[^>]*class="manager-logo"[^>]*>', page)
    assert match, "the current logo is not shown"
    assert 'alt="Shop One mark"' in match.group(0)
    assert 'src="/media/' in match.group(0) or 'src="/' in match.group(0)
    assert "data:" not in match.group(0)
    assert AuditLog.objects.filter(action="vendor.logo_changed").count() == 1


def test_an_unsafe_file_shows_an_error_summary_and_stores_nothing(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    response = _post_logo(client, logo=_upload("logo.png", b"<?php echo 1; ?>"), logo_alt="x")
    assert response.status_code == 200
    page = response.text
    assert 'class="error-summary"' in page
    assert 'href="#id_logo"' in page
    vendor.refresh_from_db()
    assert vendor.logo is None
    assert not MediaImage.objects.exists()


def test_missing_alt_text_is_a_field_error(client: Client, owner: User) -> None:
    verified_login(client, owner)
    response = _post_logo(client, logo=_upload(), logo_alt="")
    assert response.status_code == 200
    assert 'href="#id_logo_alt"' in response.text
    assert not MediaImage.objects.exists()


def test_the_owner_removes_the_logo(client: Client, owner: User, vendor: Vendor) -> None:
    verified_login(client, owner)
    _post_logo(client, logo=_upload(), logo_alt="Mark")
    response = _post_logo(client, remove="on")
    assert response.status_code == 302
    vendor.refresh_from_db()
    assert vendor.logo is None
    assert 'class="manager-logo"' not in client.get(PROFILE).text


def test_staff_cannot_upload_a_logo(client: Client, vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, staff)
    assert _post_logo(client, logo=_upload(), logo_alt="x").status_code == 404
    assert not MediaImage.objects.exists()


def test_a_suspended_vendor_gets_no_logo_form(client: Client, owner: User, vendor: Vendor) -> None:
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    verified_login(client, owner)
    assert 'type="file"' not in client.get(PROFILE).text
    refused = _post_logo(client, logo=_upload(), logo_alt="x")
    assert refused.status_code == 403
    assert not MediaImage.objects.exists()


def test_a_crafted_vendor_field_cannot_move_the_logo_to_another_vendor(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    verified_login(client, owner)
    _post_logo(client, logo=_upload(), logo_alt="Mine", vendor=str(other_vendor.pk))
    other_vendor.refresh_from_db()
    vendor.refresh_from_db()
    assert other_vendor.logo is None
    assert vendor.logo is not None


def test_the_logo_form_has_no_inline_script_or_style(client: Client, owner: User) -> None:
    verified_login(client, owner)
    _post_logo(client, logo=_upload(), logo_alt="Mark")
    _, _, content = client.get(PROFILE).text.partition('class="manager-page"')
    assert content
    assert "<script" not in content
    assert 'style="' not in content
