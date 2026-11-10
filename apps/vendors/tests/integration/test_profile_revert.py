"""CORE.9 part 3: platform staff see the storefront profile history and revert a change.

The history is the `vendor.profile_changed` audit trail written by `save_storefront_profile`. A
revert needs a dedicated permission and a reason, is audited, tells the vendor owners by email and
supports a dry run. It never reverts over a later change.
"""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext, user_context
from apps.core.models import AuditLog, OutboxEmail
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import (
    PROFILE_REVERT_PERMISSION,
    profile_history,
    revert_profile_change,
    save_storefront_profile,
)
from apps.vendors.tests.factories import (
    VendorFactory,
    VendorMemberFactory,
    VendorTranslationFactory,
)

pytestmark = pytest.mark.django_db

CHANGED = "vendor.profile_changed"
REVERTED = "vendor.profile_reverted"
CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")
STAFF_CTX = AuditContext(actor_label="user:staff@example.com", source="admin")
REASON = "The refund policy contradicts the law."


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


@pytest.fixture
def staff() -> User:
    return UserFactory.create(permissions=[PROFILE_REVERT_PERMISSION])


def _change(vendor: Vendor, owner: User, language: str = "en", **values: str) -> AuditLog:
    save_storefront_profile(vendor, language, values, actor=owner, ctx=CTX)
    return AuditLog.objects.filter(action=CHANGED).latest("id")


def _row(vendor: Vendor, language: str = "en") -> VendorTranslation:
    return VendorTranslation.objects.get(vendor=vendor, language=language)


def test_the_revert_restores_the_previous_text_and_is_audited(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", refund_policy="Fair", tagline="T")
    entry = _change(vendor, owner, refund_policy="No refunds ever")
    changes = revert_profile_change(
        entry.public_id, REASON, actor=staff, ctx=user_context(staff, None)
    )
    row = _row(vendor)
    assert (row.refund_policy, row.tagline) == ("Fair", "T")
    assert not changes.is_empty
    log = AuditLog.objects.get(action=REVERTED)
    assert log.actor_id == staff.pk
    assert log.before is not None
    assert log.after is not None
    assert (log.before["refund_policy"], log.after["refund_policy"]) == ("No refunds ever", "Fair")
    assert log.after["reason"] == REASON
    assert log.after["reverts"] == str(entry.public_id)
    assert log.vendor_key == str(vendor.public_id)


def test_the_vendor_owners_get_an_email_with_the_reason(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorMemberFactory.create(vendor=vendor, role="staff")  # not told: cannot edit the profile
    VendorTranslationFactory.create(vendor=vendor, language="en", refund_policy="Fair")
    entry = _change(vendor, owner, refund_policy="No refunds ever")
    revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    mail = OutboxEmail.objects.get(template_key="vendors.profile_reverted")
    assert mail.recipient == owner.email
    assert REASON in mail.context  # snapshot of the values the email shows


def test_a_reason_is_required(vendor: Vendor, owner: User, staff: User) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    entry = _change(vendor, owner, tagline="New")
    for reason in ("", "   "):
        with pytest.raises(ValidationError):
            revert_profile_change(entry.public_id, reason, actor=staff, ctx=STAFF_CTX)
    assert _row(vendor).tagline == "New"
    assert not AuditLog.objects.filter(action=REVERTED).exists()


def test_without_the_permission_nothing_happens(vendor: Vendor, owner: User) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    entry = _change(vendor, owner, tagline="New")
    plain = UserFactory.create(staff=True)
    for actor in (owner, plain, UserFactory.create()):
        with pytest.raises(PermissionDenied):
            revert_profile_change(entry.public_id, REASON, actor=actor, ctx=STAFF_CTX)
    assert _row(vendor).tagline == "New"


def test_a_dry_run_returns_the_change_and_writes_nothing(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    entry = _change(vendor, owner, tagline="New")
    changes = revert_profile_change(
        entry.public_id, REASON, actor=staff, ctx=STAFF_CTX, dry_run=True
    )
    assert not changes.is_empty
    assert _row(vendor).tagline == "New"
    assert not AuditLog.objects.filter(action=REVERTED).exists()
    assert not OutboxEmail.objects.filter(template_key="vendors.profile_reverted").exists()


def test_a_later_change_blocks_the_revert_of_an_earlier_one(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    first = _change(vendor, owner, tagline="Second")
    _change(vendor, owner, tagline="Third")
    with pytest.raises(ValidationError):
        revert_profile_change(first.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    assert _row(vendor).tagline == "Third"


def test_a_change_is_reverted_once(vendor: Vendor, owner: User, staff: User) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    entry = _change(vendor, owner, tagline="New")
    revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    with pytest.raises(ValidationError):
        revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)


def test_reverting_a_first_save_blanks_the_texts(vendor: Vendor, owner: User, staff: User) -> None:
    entry = _change(vendor, owner, language="it", tagline="Ciao", refund_policy="Nessuno")
    revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    row = _row(vendor, "it")
    assert (row.tagline, row.refund_policy) == ("", "")


def test_only_the_changed_fields_are_touched(vendor: Vendor, owner: User, staff: User) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="A", description="D")
    entry = _change(vendor, owner, tagline="B")
    VendorTranslation.objects.filter(vendor=vendor, language="en").update(description="Edited")
    revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    row = _row(vendor)
    assert (row.tagline, row.description) == ("A", "Edited")


def test_an_unknown_or_foreign_entry_is_refused(vendor: Vendor, owner: User, staff: User) -> None:
    import uuid

    other = AuditLog.objects.create(
        actor_label="x",
        source="admin",
        action="vendor.updated",
        target_model="vendors.vendor",
        target_key="k",
    )
    for public_id in (uuid.uuid4(), other.public_id):
        with pytest.raises(ValidationError):
            revert_profile_change(public_id, REASON, actor=staff, ctx=STAFF_CTX)


def test_a_suspended_vendor_can_still_be_reverted_by_staff(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    entry = _change(vendor, owner, tagline="New")
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    assert _row(vendor).tagline == "Old"
    # and the revert does not lock the owner out (conservative reading of the spec)
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.ACTIVE)
    save_storefront_profile(vendor, "en", {"tagline": "Again"}, actor=owner, ctx=CTX)


def test_history_needs_the_permission_and_filters_by_vendor(
    vendor: Vendor, owner: User, staff: User
) -> None:
    other = VendorFactory.create()
    other_owner = VendorMemberFactory.create(vendor=other, role="owner").user
    mine = _change(vendor, owner, tagline="Mine")
    theirs = _change(other, other_owner, tagline="Theirs")
    assert {e.pk for e in profile_history(staff)} == {mine.pk, theirs.pk}
    assert [e.pk for e in profile_history(staff, vendor=vendor)] == [mine.pk]
    with pytest.raises(PermissionDenied):
        profile_history(owner)
    with pytest.raises(PermissionDenied):
        profile_history(other_owner, vendor=other)


# --- D-33 and the security review of #231


def test_a_revert_of_the_source_text_sends_translations_back_to_review(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Hi", status="published")
    it = VendorTranslationFactory.create(
        vendor=vendor, language="it", tagline="Ciao", status="published", source_hash="old"
    )
    entry = _change(vendor, owner, tagline="Hello")
    VendorTranslation.objects.filter(pk=it.pk).update(status="published")  # back to published
    before_count = AuditLog.objects.filter(action="vendor.profile_translation_stale").count()
    revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    it.refresh_from_db()
    assert it.status == "machine"
    stale = AuditLog.objects.filter(action="vendor.profile_translation_stale").latest("id")
    assert AuditLog.objects.filter(action="vendor.profile_translation_stale").count() > before_count
    assert stale.target_key == f"{vendor.public_id}/it"


def test_crafted_before_values_with_markup_are_refused(
    vendor: Vendor, owner: User, staff: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    entry = _change(vendor, owner, tagline="New")
    assert entry.before is not None
    crafted = AuditLog.objects.create(
        actor_label="x",
        source="vendor",
        action=CHANGED,
        target_model=entry.target_model,
        target_key=entry.target_key,
        before={**entry.before, "tagline": "<script>x()</script>"},
        after=entry.after,
        vendor_key=entry.vendor_key,
    )
    with pytest.raises(ValidationError):
        revert_profile_change(crafted.public_id, REASON, actor=staff, ctx=STAFF_CTX)
    assert _row(vendor).tagline == "New"


@pytest.mark.parametrize(
    "after",
    [
        {},
        {"language": "en", "tagline": "x"},
        {"vendor": "not-a-uuid", "language": "en", "tagline": "x"},
        {"vendor": "00000000-0000-0000-0000-000000000000", "language": "en", "tagline": "x"},
        ["not", "a", "dict"],
    ],
)
def test_a_malformed_entry_is_refused_cleanly(staff: User, after: object) -> None:
    entry = AuditLog.objects.create(
        actor_label="x",
        source="vendor",
        action=CHANGED,
        target_model="vendors.vendortranslation",
        target_key="k",
        before=None,
        after=after,
    )
    with pytest.raises(ValidationError):
        revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_reverts_apply_once() -> None:
    import threading

    from django.db import connection

    vendor = VendorFactory.create()
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    staff = UserFactory.create(permissions=[PROFILE_REVERT_PERMISSION])
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Old")
    entry = _change(vendor, owner, tagline="New")
    results: list[str] = []

    def run() -> None:
        try:
            revert_profile_change(entry.public_id, REASON, actor=staff, ctx=STAFF_CTX)
            results.append("ok")
        except ValidationError:
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["ok", "refused"]
    assert AuditLog.objects.filter(action=REVERTED).count() == 1
