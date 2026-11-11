"""CORE.9 part 3 (owner decision Q1): the previous logo file is kept until the review window
closes or staff confirm the change, so a revert restores it.

Replacing or removing a logo no longer deletes the old image. A revert puts it back (with its
alt text); the job and the confirm path delete it, audited; after that a revert can only take the
logo down.
"""

import datetime as dt

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog, MediaImage
from apps.vendors.models import Vendor
from apps.vendors.services import (
    PROFILE_REVERT_PERMISSION,
    confirm_logo_change,
    confirmable_logo_entry_ids,
    purge_retained_logos,
    remove_vendor_logo,
    revert_logo_change,
    set_vendor_logo,
)
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.upload_corpus import png

pytestmark = pytest.mark.django_db

CHANGED = "vendor.logo_changed"
CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")
SYSTEM = AuditContext(actor_label="system:purge", source="system")
STAFF_CTX = AuditContext(actor_label="user:staff@example.com", source="admin")
REASON = "The logo is not the vendor's own."
LATER = timezone.now() + dt.timedelta(days=400)  # past any review window


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


@pytest.fixture
def staff() -> User:
    return UserFactory.create(permissions=[PROFILE_REVERT_PERMISSION])


def _set(vendor: Vendor, owner: User, alt: str = "Shop mark") -> AuditLog:
    set_vendor_logo(
        vendor, upload=SimpleUploadedFile("logo.png", png()), alt=alt, actor=owner, ctx=CTX
    )
    return AuditLog.objects.filter(action=CHANGED).latest("id")


def _replaced(vendor: Vendor, owner: User) -> tuple[int, AuditLog]:
    """A vendor with logo one replaced by logo two: (the id of one, the replace entry)."""
    _set(vendor, owner, alt="First mark")
    first = Vendor.objects.get(pk=vendor.pk).logo
    assert first is not None
    return first.pk, _set(vendor, owner, alt="Second mark")


def test_replacing_a_logo_keeps_the_previous_image(vendor: Vendor, owner: User) -> None:
    first_id, entry = _replaced(vendor, owner)
    assert MediaImage.objects.filter(pk=first_id).exists()
    assert entry.before is not None
    assert entry.before["logo"] == f"image:{first_id}"  # the history entry names it


def test_removing_a_logo_keeps_the_image_too(vendor: Vendor, owner: User) -> None:
    _set(vendor, owner)
    kept = Vendor.objects.get(pk=vendor.pk).logo
    assert kept is not None
    remove_vendor_logo(vendor, actor=owner, ctx=CTX)
    assert MediaImage.objects.filter(pk=kept.pk).exists()


def test_a_revert_of_a_replaced_logo_restores_the_previous_image_and_alt(
    vendor: Vendor, owner: User, staff: User
) -> None:
    first_id, entry = _replaced(vendor, owner)
    second = Vendor.objects.get(pk=vendor.pk).logo
    assert second is not None
    revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert vendor.logo.pk == first_id
    assert vendor.logo_alt == "First mark"
    assert not MediaImage.objects.filter(pk=second.pk).exists()  # the abusive one is gone
    log = AuditLog.objects.get(action="vendor.logo_reverted")
    assert log.after is not None
    assert log.after["logo"] == f"image:{first_id}"


def test_a_revert_dry_run_shows_the_restored_image_and_changes_nothing(
    vendor: Vendor, owner: User, staff: User
) -> None:
    first_id, entry = _replaced(vendor, owner)
    before = Vendor.objects.get(pk=vendor.pk).logo
    changes = revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX, dry_run=True)
    after = changes.changes[0].after
    assert after is not None
    assert after["logo"] == f"image:{first_id}"
    assert Vendor.objects.get(pk=vendor.pk).logo == before


def test_the_window_closing_deletes_the_old_file_and_a_revert_then_removes_the_logo(
    vendor: Vendor, owner: User, staff: User
) -> None:
    first_id, entry = _replaced(vendor, owner)
    changes = purge_retained_logos(ctx=SYSTEM, now=LATER)
    assert len(changes.changes) == 1
    assert not MediaImage.objects.filter(pk=first_id).exists()
    purged = AuditLog.objects.filter(action="vendor.logo_purged", vendor_key=entry.vendor_key)
    assert purged.exists()
    revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.logo is None  # as before the decision: nothing to restore


def test_the_job_keeps_files_inside_the_window_and_never_the_current_logo(
    vendor: Vendor, owner: User, staff: User
) -> None:
    first_id, entry = _replaced(vendor, owner)
    assert purge_retained_logos(ctx=SYSTEM).changes == []  # inside the window
    assert MediaImage.objects.filter(pk=first_id).exists()
    revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)  # first is current
    purge_retained_logos(ctx=SYSTEM, now=LATER)
    assert MediaImage.objects.filter(pk=first_id).exists()  # never delete a logo in use


def test_the_job_is_idempotent_and_has_a_dry_run(vendor: Vendor, owner: User) -> None:
    first_id, _ = _replaced(vendor, owner)
    preview = purge_retained_logos(ctx=SYSTEM, now=LATER, dry_run=True)
    assert len(preview.changes) == 1
    assert MediaImage.objects.filter(pk=first_id).exists()
    assert not AuditLog.objects.filter(action="vendor.logo_purged").exists()
    purge_retained_logos(ctx=SYSTEM, now=LATER)
    assert purge_retained_logos(ctx=SYSTEM, now=LATER).changes == []


def test_staff_confirming_deletes_the_old_file(vendor: Vendor, owner: User, staff: User) -> None:
    first_id, entry = _replaced(vendor, owner)
    confirm_logo_change(entry.public_id, actor=staff, ctx=user_context(staff, None))
    assert not MediaImage.objects.filter(pk=first_id).exists()
    log = AuditLog.objects.get(action="vendor.logo_confirmed")
    assert log.actor_id == staff.pk
    assert log.after is not None
    assert log.after["entry"] == str(entry.public_id)
    revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.logo is None


def test_confirm_is_staff_only_once_and_has_a_dry_run(
    vendor: Vendor, owner: User, staff: User
) -> None:
    first_id, entry = _replaced(vendor, owner)
    for actor in (owner, UserFactory.create(staff=True), UserFactory.create()):
        with pytest.raises(PermissionDenied):
            confirm_logo_change(entry.public_id, actor=actor, ctx=STAFF_CTX)
    confirm_logo_change(entry.public_id, actor=staff, ctx=STAFF_CTX, dry_run=True)
    assert MediaImage.objects.filter(pk=first_id).exists()
    confirm_logo_change(entry.public_id, actor=staff, ctx=STAFF_CTX)
    with pytest.raises(ValidationError):
        confirm_logo_change(entry.public_id, actor=staff, ctx=STAFF_CTX)


def test_confirm_refuses_a_change_with_nothing_kept_or_already_reverted(
    vendor: Vendor, owner: User, staff: User
) -> None:
    first = _set(vendor, owner)  # no previous image
    with pytest.raises(ValidationError):
        confirm_logo_change(first.public_id, actor=staff, ctx=STAFF_CTX)
    _, entry = _replaced(vendor, owner)
    revert_logo_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    with pytest.raises(ValidationError):
        confirm_logo_change(entry.public_id, actor=staff, ctx=STAFF_CTX)


def test_an_alt_only_edit_keeps_no_file_and_never_purges_the_one_a_later_replace_keeps(
    vendor: Vendor, owner: User, staff: User
) -> None:
    _set(vendor, owner, alt="First mark")
    kept = Vendor.objects.get(pk=vendor.pk).logo
    assert kept is not None
    set_vendor_logo(vendor, upload=None, alt="Only the alt", actor=owner, ctx=CTX)
    alt_only = AuditLog.objects.filter(action=CHANGED).latest("id")
    assert confirmable_logo_entry_ids([alt_only]) == set()  # nothing is kept for it
    with pytest.raises(ValidationError):
        confirm_logo_change(alt_only.public_id, actor=staff, ctx=STAFF_CTX)
    replace = _set(vendor, owner, alt="Second mark")
    assert confirmable_logo_entry_ids([replace]) == {str(replace.public_id)}
    # The audit log is append-only: pick the job's clock so that the window has closed for the alt
    # edit and is still open for the replacement.
    window = dt.timedelta(days=settings.VENDOR_LOGO_REVIEW_DAYS)
    assert replace.created_at > alt_only.created_at
    now = alt_only.created_at + window + (replace.created_at - alt_only.created_at) / 2
    purge_retained_logos(ctx=SYSTEM, now=now)
    assert MediaImage.objects.filter(pk=kept.pk).exists()  # still inside the replace window
    assert not AuditLog.objects.filter(
        action="vendor.logo_purged", after__entry=str(alt_only.public_id)
    ).exists()
    revert_logo_change(replace.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert vendor.logo.pk == kept.pk
    assert vendor.logo_alt == "Only the alt"


def test_a_crafted_entry_cannot_restore_an_image_of_another_vendor(
    vendor: Vendor, owner: User, staff: User
) -> None:
    other = VendorFactory.create()
    other_owner = VendorMemberFactory.create(vendor=other, role="owner").user
    _set(other, other_owner)
    foreign = Vendor.objects.get(pk=other.pk).logo
    assert foreign is not None
    entry = _set(vendor, owner)
    assert entry.before is not None
    crafted = AuditLog.objects.create(
        actor_label="x",
        source="vendor",
        action=CHANGED,
        target_model=entry.target_model,
        target_key=entry.target_key,
        before={**entry.before, "logo": f"image:{foreign.pk}"},
        after=entry.after,
        vendor_key=entry.vendor_key,
    )
    revert_logo_change(crafted.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    vendor.refresh_from_db()
    assert vendor.logo is None  # not restored from another vendor's collection
    assert MediaImage.objects.filter(pk=foreign.pk).exists()  # and not deleted either
