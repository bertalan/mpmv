"""CORE.9 part 2b: the owner uploads, replaces and removes the vendor logo.

The upload goes through the SEC.2 checks of `core.MediaImage` (content, re-encoding, safe names,
size and pixel caps) into the vendor's own Wagtail collection `vendor-<public id>`; the change
is audited, owner only, and the logo has alt text.
"""

import io

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from wagtail.models import Collection

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, MediaImage
from apps.vendors.models import Vendor
from apps.vendors.services import remove_vendor_logo, set_vendor_logo
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.upload_corpus import (
    Case,
    assert_safe_image,
    image_cases,
    name_cases,
    png,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")
ACTION = "vendor.logo_changed"


def _file(name: str = "logo.png", data: bytes | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, png() if data is None else data)


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create(display_name="Shop Logo")


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


def test_the_owner_sets_a_logo_in_the_vendors_own_collection(vendor: Vendor, owner: User) -> None:
    set_vendor_logo(vendor, upload=_file(), alt="Shop Logo mark", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert vendor.logo_alt == "Shop Logo mark"
    assert vendor.logo.collection.name == f"vendor-{vendor.public_id}"
    assert vendor.logo.licence is not None
    assert vendor.logo.licence.code == "own-work"
    assert vendor.logo.author == "Shop Logo"


def test_two_logos_of_one_vendor_share_one_collection(vendor: Vendor, owner: User) -> None:
    set_vendor_logo(vendor, upload=_file(), alt="One", actor=owner, ctx=CTX)
    set_vendor_logo(vendor, upload=_file(), alt="Two", actor=owner, ctx=CTX)
    assert Collection.objects.filter(name=f"vendor-{vendor.public_id}").count() == 1


def test_each_vendor_has_its_own_collection(owner: User, vendor: Vendor) -> None:
    other = VendorFactory.create()
    other_owner = VendorMemberFactory.create(vendor=other, role="owner").user
    set_vendor_logo(vendor, upload=_file(), alt="A", actor=owner, ctx=CTX)
    set_vendor_logo(other, upload=_file(), alt="B", actor=other_owner, ctx=CTX)
    vendor.refresh_from_db()
    other.refresh_from_db()
    assert vendor.logo is not None
    assert other.logo is not None
    assert vendor.logo.collection_id != other.logo.collection_id


def test_replacing_the_logo_deletes_the_old_image(vendor: Vendor, owner: User) -> None:
    set_vendor_logo(vendor, upload=_file(), alt="Old", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    old_pk = vendor.logo.pk
    set_vendor_logo(vendor, upload=_file(), alt="New", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert vendor.logo.pk != old_pk
    assert not MediaImage.objects.filter(pk=old_pk).exists()


def test_changing_only_the_alt_text_keeps_the_image(vendor: Vendor, owner: User) -> None:
    set_vendor_logo(vendor, upload=_file(), alt="Old", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    pk = vendor.logo.pk
    set_vendor_logo(vendor, upload=None, alt="Better description", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert vendor.logo.pk == pk
    assert vendor.logo_alt == "Better description"


def test_removing_the_logo_deletes_the_image_and_clears_the_fields(
    vendor: Vendor, owner: User
) -> None:
    set_vendor_logo(vendor, upload=_file(), alt="Mark", actor=owner, ctx=CTX)
    remove_vendor_logo(vendor, actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is None
    assert vendor.logo_alt == ""
    assert not MediaImage.objects.exists()


def test_the_change_is_audited_with_before_and_after(vendor: Vendor, owner: User) -> None:
    set_vendor_logo(vendor, upload=_file(), alt="First", actor=owner, ctx=CTX)
    set_vendor_logo(vendor, upload=_file(), alt="Second", actor=owner, ctx=CTX)
    remove_vendor_logo(vendor, actor=owner, ctx=CTX)
    entries = list(AuditLog.objects.filter(action=ACTION).order_by("id"))
    assert len(entries) == 3
    first, second, removed = entries
    assert first.before is not None
    assert first.after is not None
    assert first.before["logo"] is None
    assert first.after["logo_alt"] == "First"
    assert second.before is not None
    assert second.after is not None
    assert second.before["logo_alt"] == "First"
    assert second.after["logo_alt"] == "Second"
    assert removed.after is not None
    assert removed.after["logo"] is None
    assert all(e.vendor_key == str(vendor.public_id) for e in entries)
    assert all(e.target_key == str(vendor.public_id) for e in entries)


def test_alt_text_is_required_with_a_new_logo(vendor: Vendor, owner: User) -> None:
    for alt in ("", "   "):
        with pytest.raises(ValidationError):
            set_vendor_logo(vendor, upload=_file(), alt=alt, actor=owner, ctx=CTX)
    assert not MediaImage.objects.exists()


@pytest.mark.parametrize("alt", ["<b>logo</b>", "x" * 126, "a\x00b"])
def test_alt_text_is_short_plain_text(vendor: Vendor, owner: User, alt: str) -> None:
    with pytest.raises(ValidationError):
        set_vendor_logo(vendor, upload=_file(), alt=alt, actor=owner, ctx=CTX)
    assert not MediaImage.objects.exists()


def test_without_upload_or_existing_logo_there_is_nothing_to_save(
    vendor: Vendor, owner: User
) -> None:
    with pytest.raises(ValidationError):
        set_vendor_logo(vendor, upload=None, alt="Orphan alt", actor=owner, ctx=CTX)


def test_staff_other_owners_strangers_and_platform_staff_cannot(vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    other_owner = VendorMemberFactory.create(vendor=VendorFactory.create(), role="owner").user
    platform = UserFactory.create(permissions=["core.platform_owner"])
    for actor in (staff, other_owner, UserFactory.create(), platform):
        with pytest.raises(PermissionDenied):
            set_vendor_logo(vendor, upload=_file(), alt="x", actor=actor, ctx=CTX)
        with pytest.raises(PermissionDenied):
            remove_vendor_logo(vendor, actor=actor, ctx=CTX)
    assert not MediaImage.objects.exists()
    assert not AuditLog.objects.filter(action=ACTION).exists()


@pytest.mark.parametrize("status", [Vendor.Status.SUSPENDED, Vendor.Status.CLOSED])
def test_a_suspended_or_closed_vendor_cannot_change_its_logo(
    vendor: Vendor, owner: User, status: str
) -> None:
    Vendor.objects.filter(pk=vendor.pk).update(status=status)
    with pytest.raises(PermissionDenied):
        set_vendor_logo(vendor, upload=_file(), alt="x", actor=owner, ctx=CTX)


def test_a_dry_run_stores_nothing(vendor: Vendor, owner: User) -> None:
    changes = set_vendor_logo(
        vendor, upload=_file(), alt="Preview", actor=owner, ctx=CTX, dry_run=True
    )
    assert not changes.is_empty
    vendor.refresh_from_db()
    assert vendor.logo is None
    assert not MediaImage.objects.exists()
    assert not Collection.objects.filter(name__startswith="vendor-").exists()
    assert not AuditLog.objects.filter(action=ACTION).exists()


def test_a_refused_file_keeps_the_current_logo(vendor: Vendor, owner: User) -> None:
    set_vendor_logo(vendor, upload=_file(), alt="Keep me", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    pk = vendor.logo.pk
    with pytest.raises(ValidationError):
        set_vendor_logo(
            vendor, upload=_file("logo.png", b"<?php echo 1; ?>"), alt="Evil", actor=owner, ctx=CTX
        )
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert vendor.logo.pk == pk
    assert vendor.logo_alt == "Keep me"


def test_logo_changes_use_the_shared_save_rate_limit(
    vendor: Vendor, owner: User, settings: object
) -> None:
    settings.VENDOR_PROFILE_SAVE_LIMIT = 1  # type: ignore[attr-defined]
    set_vendor_logo(vendor, upload=_file(), alt="One", actor=owner, ctx=CTX)
    with pytest.raises(ValidationError):
        set_vendor_logo(vendor, upload=_file(), alt="Two", actor=owner, ctx=CTX)


# --- The SEC.2 corpus against this upload path


@pytest.mark.parametrize("case", image_cases() + name_cases(), ids=lambda c: c.id)
def test_the_logo_upload_refuses_or_safely_stores_the_upload_corpus(case: Case) -> None:
    if case.skip:
        pytest.skip(case.skip)
    if case.xfail:
        pytest.xfail(case.xfail)
    vendor = VendorFactory.create()
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    upload = SimpleUploadedFile(case.filename, case.data)
    try:
        set_vendor_logo(vendor, upload=upload, alt="Logo", actor=owner, ctx=CTX)
    except ValidationError as refusal:
        errors = set(refusal.message_dict)
        assert errors <= {"logo", "file"}, errors
        vendor.refresh_from_db()
        assert vendor.logo is None, case.why
        assert not MediaImage.objects.exists(), case.why
        assert case.verdict == "refuse", f"{case.id} was refused but should be stored"
        return
    assert case.verdict == "store", f"{case.why}: stored"
    vendor.refresh_from_db()
    assert vendor.logo is not None
    with vendor.logo.file.open("rb") as handle:
        assert_safe_image(vendor.logo.file.name, handle.read(), case)


def test_a_stored_logo_is_a_real_reencoded_image(vendor: Vendor, owner: User) -> None:
    from PIL import Image

    set_vendor_logo(vendor, upload=_file(), alt="Mark", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    with vendor.logo.file.open("rb") as handle, Image.open(io.BytesIO(handle.read())) as image:
        assert image.size == (vendor.logo.width, vendor.logo.height)


# --- Review fixes (#208)


def test_the_first_logo_locks_the_root_collection_before_adding_the_child(
    vendor: Vendor, owner: User
) -> None:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        set_vendor_logo(vendor, upload=_file(), alt="Mark", actor=owner, ctx=CTX)
    sql = [q["sql"] for q in queries.captured_queries]
    lock = next(i for i, s in enumerate(sql) if "FOR UPDATE" in s and "wagtailcore_collection" in s)
    insert = next(
        i for i, s in enumerate(sql) if s.startswith("INSERT") and "wagtailcore_collection" in s
    )
    assert lock < insert, "two vendors' first uploads could pick the same tree path"


def test_a_duplicate_collection_name_is_resolved_to_the_oldest(vendor: Vendor, owner: User) -> None:
    root = Collection.get_first_root_node()
    oldest = root.add_child(name=f"vendor-{vendor.public_id}")
    root.add_child(name=f"vendor-{vendor.public_id}")
    set_vendor_logo(vendor, upload=_file(), alt="Mark", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is not None
    assert vendor.logo.collection_id == oldest.pk


def _share_logo(vendor: Vendor, owner: User) -> Vendor:
    """Another vendor whose logo row is the same image as `vendor`'s."""
    set_vendor_logo(vendor, upload=_file(), alt="Shared", actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    other = VendorFactory.create()
    Vendor.objects.filter(pk=other.pk).update(logo=vendor.logo, logo_alt="Shared")
    return other


def test_replacing_a_logo_another_vendor_still_uses_only_unlinks_it(
    vendor: Vendor, owner: User
) -> None:
    other = _share_logo(vendor, owner)
    shared_pk = other.__class__.objects.get(pk=other.pk).logo_id
    set_vendor_logo(vendor, upload=_file(), alt="Own now", actor=owner, ctx=CTX)
    assert MediaImage.objects.filter(pk=shared_pk).exists()
    other.refresh_from_db()
    assert other.logo_id == shared_pk


def test_removing_a_logo_another_vendor_still_uses_only_unlinks_it(
    vendor: Vendor, owner: User
) -> None:
    other = _share_logo(vendor, owner)
    shared_pk = Vendor.objects.get(pk=other.pk).logo_id
    remove_vendor_logo(vendor, actor=owner, ctx=CTX)
    vendor.refresh_from_db()
    assert vendor.logo is None
    assert MediaImage.objects.filter(pk=shared_pk).exists()


def test_a_missing_own_work_licence_is_a_clear_error_not_a_500(vendor: Vendor, owner: User) -> None:
    from apps.core.models import ImageLicence

    ImageLicence.objects.filter(code="own-work").delete()
    with pytest.raises(ValidationError) as caught:
        set_vendor_logo(vendor, upload=_file(), alt="Mark", actor=owner, ctx=CTX)
    assert "logo" in caught.value.message_dict


def test_refused_uploads_count_against_the_rate_limit(
    vendor: Vendor, owner: User, settings: object
) -> None:
    settings.VENDOR_PROFILE_SAVE_LIMIT = 2  # type: ignore[attr-defined]
    bad = _file("logo.png", b"<?php echo 1; ?>")
    for _ in range(2):
        with pytest.raises(ValidationError) as caught:
            set_vendor_logo(vendor, upload=bad, alt="Evil", actor=owner, ctx=CTX)
        assert "Too many" not in str(caught.value)
    with pytest.raises(ValidationError) as caught:
        set_vendor_logo(vendor, upload=_file(), alt="Good", actor=owner, ctx=CTX)
    assert "Too many" in str(caught.value)
    assert not MediaImage.objects.exists()


def test_a_stored_file_is_removed_when_a_later_step_fails(
    vendor: Vendor, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.vendors import services

    stored: list[tuple[object, str]] = []
    original = MediaImage.save

    def spying_save(self: MediaImage, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)  # type: ignore[arg-type]
        stored.append((self.file.storage, self.file.name))

    def failing_notice(*args: object, **kwargs: object) -> None:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(MediaImage, "save", spying_save)
    monkeypatch.setattr(services, "_notify_profile_changed", failing_notice)
    with pytest.raises(RuntimeError):
        set_vendor_logo(vendor, upload=_file(), alt="Mark", actor=owner, ctx=CTX)
    assert stored
    storage, name = stored[0]
    assert not storage.exists(name)  # type: ignore[attr-defined]
    assert not MediaImage.objects.exists()
