"""CORE.9 part 3 on top of VEN.29 (rich text) and VEN.31 (media): what a revert restores.

The storefront description and policies are cleaned rich text now, and the logo is a vendor image.
A revert puts the previous HTML back (cleaned again, media rule checked), refuses a plain-text
reason with markup, and also reverts the owner's logo changes (`vendor.logo_changed`).
"""

import uuid

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import (
    PROFILE_REVERT_PERMISSION,
    profile_history,
    revert_logo_change,
    revert_profile_change,
    save_storefront_profile,
    set_vendor_logo,
)
from apps.vendors.tests.factories import (
    VendorFactory,
    VendorMemberFactory,
    VendorTranslationFactory,
)
from tests.upload_corpus import png

pytestmark = pytest.mark.django_db

CHANGED = "vendor.profile_changed"
LOGO_CHANGED = "vendor.logo_changed"
LOGO_REVERTED = "vendor.logo_reverted"
CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")
STAFF_CTX = AuditContext(actor_label="user:staff@example.com", source="admin")
REASON = "The logo is not the vendor's own."


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


@pytest.fixture
def staff() -> User:
    return UserFactory.create(permissions=[PROFILE_REVERT_PERMISSION])


def _text_change(vendor: Vendor, owner: User, **values: str) -> AuditLog:
    save_storefront_profile(vendor, "en", values, actor=owner, ctx=CTX)
    return AuditLog.objects.filter(action=CHANGED).latest("id")


def _logo(vendor: Vendor, owner: User, alt: str = "Shop mark", upload: bool = True) -> AuditLog:
    file = SimpleUploadedFile("logo.png", png()) if upload else None
    set_vendor_logo(vendor, upload=file, alt=alt, actor=owner, ctx=CTX)
    return AuditLog.objects.filter(action=LOGO_CHANGED).latest("id")


# --- rich text (VEN.29)


def test_a_revert_restores_the_previous_rich_text(vendor: Vendor, owner: User, staff: User) -> None:
    VendorTranslationFactory.create(
        vendor=vendor, language="en", description="<p>Hand <strong>made</strong></p>"
    )
    entry = _text_change(vendor, owner, description="<p>Buy <em>now</em> elsewhere</p>")
    revert_profile_change(entry.public_id, "Spam link.", actor=staff, ctx=STAFF_CTX)
    row = VendorTranslation.objects.get(vendor=vendor, language="en")
    assert row.description == "<p>Hand <strong>made</strong></p>"
    log = AuditLog.objects.get(action="vendor.profile_reverted")
    assert log.after is not None
    assert log.after["description"] == "<p>Hand <strong>made</strong></p>"


def test_the_restored_rich_text_goes_through_the_allow_list_again(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", description="<p>Old</p>")
    entry = _text_change(vendor, owner, description="<p>New</p>")
    assert entry.before is not None
    crafted = AuditLog.objects.create(
        actor_label="x",
        source="vendor",
        action=CHANGED,
        target_model=entry.target_model,
        target_key=entry.target_key,
        before={**entry.before, "description": '<p onclick="x()">Old</p><script>x()</script>'},
        after=entry.after,
        vendor_key=entry.vendor_key,
    )
    revert_profile_change(crafted.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    stored = VendorTranslation.objects.get(vendor=vendor, language="en").description
    assert "<script" not in stored
    assert "onclick" not in stored
    assert "Old" in stored


def test_a_restored_text_embedding_media_outside_the_vendor_is_refused(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", description="<p>Old</p>")
    entry = _text_change(vendor, owner, description="<p>New</p>")
    assert entry.before is not None
    crafted = AuditLog.objects.create(
        actor_label="x",
        source="vendor",
        action=CHANGED,
        target_model=entry.target_model,
        target_key=entry.target_key,
        before={
            **entry.before,
            "description": '<embed embedtype="image" id="999999" alt="" format="left"/>',
        },
        after=entry.after,
        vendor_key=entry.vendor_key,
    )
    with pytest.raises(ValidationError):
        revert_profile_change(crafted.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    assert VendorTranslation.objects.get(vendor=vendor, language="en").description == "<p>New</p>"


def test_the_reason_is_plain_text_even_though_the_description_is_rich(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    entry = _text_change(vendor, owner, tagline="New")
    with pytest.raises(ValidationError):
        revert_profile_change(entry.public_id, "<b>bold</b> reason", actor=staff, ctx=STAFF_CTX)
    assert not AuditLog.objects.filter(action="vendor.profile_reverted").exists()


# --- logo (VEN.31, CORE.9 2b)


def test_reverting_the_first_logo_removes_it(vendor: Vendor, owner: User, staff: User) -> None:
    entry = _logo(vendor, owner)
    changes = revert_logo_change(
        entry.public_id, REASON, actor=staff, ctx=user_context(staff, None)
    )
    vendor.refresh_from_db()
    assert vendor.logo is None
    assert vendor.logo_alt == ""
    assert not changes.is_empty
    log = AuditLog.objects.get(action=LOGO_REVERTED)
    assert log.actor_id == staff.pk
    assert log.after is not None
    assert log.after["reason"] == REASON
    assert log.after["reverts"] == str(entry.public_id)
    assert log.after["logo"] is None
    assert log.vendor_key == str(vendor.public_id)


def test_reverting_an_alt_text_change_restores_the_alt_and_keeps_the_logo(
    vendor: Vendor, owner: User, staff: User
) -> None:
    _logo(vendor, owner, alt="Shop mark")
    entry = _logo(vendor, owner, alt="Buy pills here", upload=False)
    revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert vendor.logo_alt == "Shop mark"


def test_a_logo_revert_tells_the_owners_and_needs_a_reason(
    vendor: Vendor, owner: User, staff: User
) -> None:
    entry = _logo(vendor, owner)
    with pytest.raises(ValidationError):
        revert_logo_change(entry.public_id, "  ", actor=staff, ctx=STAFF_CTX)
    revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    mail = OutboxEmail.objects.get(template_key="vendors.profile_reverted")
    assert mail.recipient == owner.email
    assert REASON in mail.context


def test_a_logo_revert_is_staff_only_and_a_dry_run_writes_nothing(
    vendor: Vendor, owner: User, staff: User
) -> None:
    entry = _logo(vendor, owner)
    for actor in (owner, UserFactory.create(staff=True), UserFactory.create()):
        with pytest.raises(PermissionDenied):
            revert_logo_change(entry.public_id, REASON, actor=actor, ctx=STAFF_CTX)
    changes = revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX, dry_run=True)
    assert not changes.is_empty
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert not AuditLog.objects.filter(action=LOGO_REVERTED).exists()


def test_a_later_logo_change_or_a_second_revert_is_refused(
    vendor: Vendor, owner: User, staff: User
) -> None:
    first = _logo(vendor, owner, alt="One")
    _logo(vendor, owner, alt="Two", upload=False)
    with pytest.raises(ValidationError):
        revert_logo_change(first.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.logo_alt == "Two"
    second = AuditLog.objects.filter(action=LOGO_CHANGED).latest("id")
    revert_logo_change(second.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    with pytest.raises(ValidationError):
        revert_logo_change(second.public_id, REASON, actor=staff, ctx=STAFF_CTX)


def test_unknown_or_profile_entries_are_not_logo_entries(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    text = _text_change(vendor, owner, tagline="New")
    for public_id in (uuid.uuid4(), text.public_id):
        with pytest.raises(ValidationError):
            revert_logo_change(public_id, REASON, actor=staff, ctx=STAFF_CTX)


def test_the_history_lists_logo_changes_and_logo_reverts(
    vendor: Vendor, owner: User, staff: User
) -> None:
    entry = _logo(vendor, owner)
    revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    actions = [e.action for e in profile_history(staff, vendor=vendor)]
    assert actions == [LOGO_REVERTED, LOGO_CHANGED]
    with pytest.raises(PermissionDenied):
        profile_history(owner)
