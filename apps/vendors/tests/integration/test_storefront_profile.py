"""CORE.9 part 2: the owner edits the storefront profile (texts and policies of one language).

Staff and non-members cannot; the change is audited with before and after, the moderators get a
post-moderation notice through the outbox (to the platform support address until the CORE.18
roles exist), and a dry run writes nothing.
"""

import datetime as dt

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.core.tests.factories import PlatformOperatorFactory
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.services import save_storefront_profile
from apps.vendors.tests.factories import (
    VendorFactory,
    VendorMemberFactory,
    VendorTranslationFactory,
)

pytestmark = pytest.mark.django_db

ACTION = "vendor.profile_changed"
CTX = AuditContext(actor_label="user:owner@example.com", source="vendor")


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create()


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


def _operator(**emails: str) -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), **emails
    )


def test_the_owner_changes_the_texts_and_policies(vendor: Vendor, owner: User) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", refund_policy="Old refund")
    values = {"tagline": "New tagline", "refund_policy": "New refund"}
    save_storefront_profile(vendor, "en", values, actor=owner, ctx=CTX)
    row = VendorTranslation.objects.get(vendor=vendor, language="en")
    assert (row.tagline, row.refund_policy) == ("New tagline", "New refund")


def test_a_language_without_a_row_gets_one(vendor: Vendor, owner: User) -> None:
    save_storefront_profile(vendor, "it", {"tagline": "Ciao"}, actor=owner, ctx=CTX)
    assert VendorTranslation.objects.get(vendor=vendor, language="it").tagline == "Ciao"


def test_the_audit_entry_has_before_and_after_and_names_the_vendor(
    vendor: Vendor, owner: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", refund_policy="Old refund")
    save_storefront_profile(vendor, "en", {"refund_policy": "New refund"}, actor=owner, ctx=CTX)
    entry = AuditLog.objects.get(action=ACTION)
    assert entry.before is not None
    assert entry.after is not None
    assert entry.before["refund_policy"] == "Old refund"
    assert entry.after["refund_policy"] == "New refund"
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.actor_label == CTX.actor_label


def test_staff_cannot_edit_the_profile(vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    with pytest.raises(PermissionDenied):
        save_storefront_profile(vendor, "en", {"tagline": "x"}, actor=staff, ctx=CTX)
    assert not VendorTranslation.objects.filter(vendor=vendor).exists()
    assert not AuditLog.objects.filter(action=ACTION).exists()


def test_the_owner_of_another_vendor_and_a_stranger_cannot_edit_it(vendor: Vendor) -> None:
    other_owner = VendorMemberFactory.create(vendor=VendorFactory.create(), role="owner").user
    for actor in (other_owner, UserFactory.create()):
        with pytest.raises(PermissionDenied):
            save_storefront_profile(vendor, "en", {"tagline": "x"}, actor=actor, ctx=CTX)


@pytest.mark.parametrize("status", [Vendor.Status.CLOSED, Vendor.Status.SUSPENDED])
def test_a_closed_or_suspended_vendor_cannot_be_edited(
    vendor: Vendor, owner: User, status: str
) -> None:
    Vendor.objects.filter(pk=vendor.pk).update(status=status)
    with pytest.raises(PermissionDenied):
        save_storefront_profile(vendor, "en", {"tagline": "x"}, actor=owner, ctx=CTX)


def test_only_the_profile_fields_and_known_languages_are_accepted(
    vendor: Vendor, owner: User
) -> None:
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "en", {"language": "de"}, actor=owner, ctx=CTX)
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "xx", {"tagline": "x"}, actor=owner, ctx=CTX)


def test_a_value_over_the_field_limit_is_refused(vendor: Vendor, owner: User) -> None:
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "en", {"tagline": "x" * 201}, actor=owner, ctx=CTX)
    assert not AuditLog.objects.filter(action=ACTION).exists()


def test_the_moderators_are_told_through_the_outbox(vendor: Vendor, owner: User) -> None:
    _operator(support_email="help@platform.example")
    values = {"shipping_policy": "Ships in 3 days"}
    save_storefront_profile(vendor, "en", values, actor=owner, ctx=CTX)
    mail = OutboxEmail.objects.get()
    assert mail.recipient == "help@platform.example"
    assert mail.template_key == "vendors.profile_changed"


def test_no_email_without_an_operator_address_but_the_change_stands(
    vendor: Vendor, owner: User
) -> None:
    _operator()
    save_storefront_profile(vendor, "en", {"tagline": "Kept"}, actor=owner, ctx=CTX)
    assert not OutboxEmail.objects.exists()
    assert AuditLog.objects.filter(action=ACTION).count() == 1


def test_an_unchanged_save_writes_no_audit_entry_and_sends_no_email(
    vendor: Vendor, owner: User
) -> None:
    _operator(support_email="help@platform.example")
    VendorTranslationFactory.create(
        vendor=vendor, language="en", tagline="Same", status="published"
    )
    changes = save_storefront_profile(vendor, "en", {"tagline": "Same"}, actor=owner, ctx=CTX)
    assert changes.is_empty
    assert not AuditLog.objects.filter(action=ACTION).exists()
    assert not OutboxEmail.objects.exists()


def test_a_dry_run_returns_the_changes_and_writes_nothing(vendor: Vendor, owner: User) -> None:
    _operator(support_email="help@platform.example")
    changes = save_storefront_profile(
        vendor, "en", {"tagline": "Preview"}, actor=owner, ctx=CTX, dry_run=True
    )
    assert not changes.is_empty
    assert not VendorTranslation.objects.filter(vendor=vendor).exists()
    assert not AuditLog.objects.filter(action=ACTION).exists()
    assert not OutboxEmail.objects.exists()


def test_another_vendors_translation_is_untouched(vendor: Vendor, owner: User) -> None:
    other = VendorTranslationFactory.create(language="en", tagline="Theirs")
    save_storefront_profile(vendor, "en", {"tagline": "Mine"}, actor=owner, ctx=CTX)
    other.refresh_from_db()
    assert other.tagline == "Theirs"
