"""CORE.9 part 2: the storefront profile page of the shop space, for the owner only.

Staff get 404 (and no menu entry), the form is accessible (CMP.9: labels, error summary tied to
the fields), and every change goes through the audited service.
"""

import re

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.tests.factories import VendorMemberFactory, VendorTranslationFactory

from .conftest import SHOP

pytestmark = pytest.mark.django_db

PROFILE = f"{SHOP}profile/"
FIELDS = ("tagline", "description", "shipping_policy", "refund_policy", "cancellation_policy")


@pytest.fixture
def staff_member(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="staff").user


def test_the_owner_sees_the_form_and_the_menu_entry(client: Client, owner: User) -> None:
    verified_login(client, owner)
    response = client.get(PROFILE)
    assert response.status_code == 200
    for name in FIELDS:
        assert f'name="{name}"' in response.text
    assert f'href="{PROFILE}"' in client.get(SHOP).text


def test_staff_get_404_and_see_no_menu_entry(client: Client, staff_member: User) -> None:
    verified_login(client, staff_member)
    assert client.get(PROFILE).status_code == 404
    assert client.post(PROFILE, {"tagline": "x"}).status_code == 404
    assert PROFILE not in client.get(SHOP).text


def test_the_owner_saves_and_the_change_is_audited(
    client: Client, owner: User, vendor: Vendor
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    verified_login(client, owner)
    response = client.post(PROFILE, {"language": "en", "tagline": "Fresh"})
    assert response.status_code == 302
    assert VendorTranslation.objects.get(vendor=vendor, language="en").tagline == "Fresh"
    entry = AuditLog.objects.get(action="vendor.profile_changed")
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["tagline"], entry.after["tagline"]) == ("Old", "Fresh")
    assert entry.actor_id == owner.pk


def test_a_too_long_tagline_shows_an_error_summary_tied_to_the_field(
    client: Client, owner: User
) -> None:
    verified_login(client, owner)
    response = client.post(PROFILE, {"language": "en", "tagline": "x" * 201})
    assert response.status_code == 200
    page = response.text
    assert 'class="error-summary"' in page
    assert 'href="#id_tagline"' in page
    assert 'aria-invalid="true"' in page
    assert not AuditLog.objects.filter(action="vendor.profile_changed").exists()


def test_every_field_has_a_label(client: Client, owner: User) -> None:
    verified_login(client, owner)
    page = client.get(PROFILE).text
    for name in FIELDS:
        assert f'for="id_{name}"' in page


def test_the_form_shows_the_chosen_language_and_refuses_an_unknown_one(
    client: Client, owner: User, vendor: Vendor
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="it", tagline="Ciao negozio")
    verified_login(client, owner)
    assert "Ciao negozio" in client.get(f"{PROFILE}?language=it").text
    assert client.get(f"{PROFILE}?language=xx").status_code == 404


def test_another_vendors_texts_never_show_and_cannot_be_posted(
    client: Client, owner: User, other_vendor: Vendor
) -> None:
    theirs = VendorTranslationFactory.create(vendor=other_vendor, language="en", tagline="Rival")
    verified_login(client, owner)
    assert "Rival" not in client.get(PROFILE).text
    client.post(PROFILE, {"language": "en", "tagline": "Hijack", "vendor": str(other_vendor.pk)})
    theirs.refresh_from_db()
    assert theirs.tagline == "Rival"


def test_the_page_has_no_inline_script_or_style(client: Client, owner: User) -> None:
    verified_login(client, owner)
    _, _, content = client.get(PROFILE).text.partition('class="manager-page"')
    assert content
    # External scripts (Wagtail's editor, same origin) and a JSON data block are fine: no code
    # inline, so the strict CSP holds.
    assert not re.search(r"<script(?![^>]*(?:\bsrc=|type=\"application/json\"))", content)
    assert 'style="' not in content


def test_a_suspended_vendor_sees_the_reason_and_no_form(
    client: Client, owner: User, vendor: Vendor
) -> None:
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    verified_login(client, owner)
    page = client.get(PROFILE)
    assert page.status_code == 200
    assert "suspended" in page.text.lower()
    assert 'name="tagline"' not in page.text
    refused = client.post(PROFILE, {"language": "en", "tagline": "Sneaky"})
    assert refused.status_code == 403
    assert "suspended" in refused.text.lower()
    assert 'name="tagline"' not in refused.text
    assert not VendorTranslation.objects.filter(vendor=vendor).exists()


def test_a_machine_draft_is_marked_on_the_form(client: Client, owner: User, vendor: Vendor) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="it", tagline="Bozza", status="machine")
    verified_login(client, owner)
    assert "not public" in client.get(f"{PROFILE}?language=it").text.lower()


def test_html_in_a_field_shows_an_error_and_is_not_stored(client: Client, owner: User) -> None:
    verified_login(client, owner)
    response = client.post(PROFILE, {"language": "en", "tagline": "<script>x</script>"})
    assert response.status_code == 200
    assert 'href="#id_tagline"' in response.text
    assert not VendorTranslation.objects.exists()


def test_html_in_a_rich_text_field_is_cleaned_and_stored(client: Client, owner: User) -> None:
    verified_login(client, owner)
    html = "<p>ok <b>bold</b></p><script>x()</script>"
    response = client.post(PROFILE, {"language": "en", "description": html})
    assert response.status_code == 302
    stored = VendorTranslation.objects.get().description
    assert stored == "<p>ok <b>bold</b></p>"


def test_stored_text_is_escaped_on_output(client: Client, owner: User, vendor: Vendor) -> None:
    VendorTranslationFactory.create(
        vendor=vendor, language="en", tagline="Fish & <chips>", status="published"
    )
    verified_login(client, owner)
    page = client.get(PROFILE).text
    assert "Fish &amp; &lt;chips&gt;" in page
    assert "<chips>" not in page


def test_too_many_saves_show_a_form_error(client: Client, owner: User, settings: object) -> None:
    settings.VENDOR_PROFILE_SAVE_LIMIT = 1  # type: ignore[attr-defined]
    verified_login(client, owner)
    assert client.post(PROFILE, {"language": "en", "tagline": "one"}).status_code == 302
    response = client.post(PROFILE, {"language": "en", "tagline": "two"})
    assert response.status_code == 200
    assert 'class="error-summary"' in response.text


def test_saving_a_machine_draft_unchanged_says_it_is_still_a_draft(
    client: Client, owner: User, vendor: Vendor
) -> None:
    VendorTranslationFactory.create(
        vendor=vendor,
        language="it",
        tagline="Bozza",
        description="",
        shipping_policy="",
        refund_policy="",
        cancellation_policy="",
        status="machine",
    )
    verified_login(client, owner)
    response = client.post(PROFILE, {"language": "it", "tagline": "Bozza"}, follow=True)
    text = response.text.lower()
    assert "still a draft" in text
    assert "edit it to publish it" in text


def test_a_nul_byte_posted_to_the_page_is_a_form_error(client: Client, owner: User) -> None:
    verified_login(client, owner)
    response = client.post(PROFILE, {"language": "en", "tagline": "a\x00b"})
    assert response.status_code == 200
    assert 'class="error-summary"' in response.text
