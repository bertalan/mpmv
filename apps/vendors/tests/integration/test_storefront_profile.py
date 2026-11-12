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


# --- Review fixes (#204): caps, rate limit, plain text, drafts, cascade, races


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("description", 10_000),
        ("shipping_policy", 5_000),
        ("refund_policy", 5_000),
        ("cancellation_policy", 5_000),
    ],
)
def test_long_texts_are_capped_at_the_service(
    vendor: Vendor, owner: User, field: str, limit: int
) -> None:
    save_storefront_profile(vendor, "en", {field: "x" * limit}, actor=owner, ctx=CTX)
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "en", {field: "x" * (limit + 1)}, actor=owner, ctx=CTX)


def test_the_audit_entry_holds_only_the_changed_fields(vendor: Vendor, owner: User) -> None:
    VendorTranslationFactory.create(
        vendor=vendor, language="en", description="d" * 9000, refund_policy="Old"
    )
    save_storefront_profile(vendor, "en", {"refund_policy": "New"}, actor=owner, ctx=CTX)
    entry = AuditLog.objects.get(action=ACTION)
    assert entry.before is not None
    assert entry.after is not None
    assert "description" not in entry.before
    assert "description" not in entry.after
    assert (entry.before["refund_policy"], entry.after["refund_policy"]) == ("Old", "New")
    assert entry.vendor_key == str(vendor.public_id)


def test_saves_are_rate_limited_per_owner(vendor: Vendor, owner: User, settings: object) -> None:
    settings.VENDOR_PROFILE_SAVE_LIMIT = 3  # type: ignore[attr-defined]
    _operator(support_email="help@platform.example")
    for n in range(3):
        save_storefront_profile(vendor, "en", {"tagline": f"v{n}"}, actor=owner, ctx=CTX)
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "en", {"tagline": "v4"}, actor=owner, ctx=CTX)
    assert OutboxEmail.objects.count() == 3
    assert VendorTranslation.objects.get(vendor=vendor, language="en").tagline == "v2"


def test_unchanged_saves_and_dry_runs_do_not_use_the_rate_limit(
    vendor: Vendor, owner: User, settings: object
) -> None:
    settings.VENDOR_PROFILE_SAVE_LIMIT = 1  # type: ignore[attr-defined]
    for _ in range(3):
        save_storefront_profile(vendor, "en", {"tagline": "a"}, actor=owner, ctx=CTX, dry_run=True)
    save_storefront_profile(vendor, "en", {"tagline": "a"}, actor=owner, ctx=CTX)
    save_storefront_profile(vendor, "en", {"tagline": "a"}, actor=owner, ctx=CTX)  # no change


def test_saving_a_machine_draft_without_edits_does_not_publish_it(
    vendor: Vendor, owner: User
) -> None:
    _operator(support_email="help@platform.example")
    VendorTranslationFactory.create(vendor=vendor, language="it", tagline="Bozza", status="machine")
    changes = save_storefront_profile(vendor, "it", {"tagline": "Bozza"}, actor=owner, ctx=CTX)
    assert changes.is_empty
    assert VendorTranslation.objects.get(vendor=vendor, language="it").status == "machine"
    assert not OutboxEmail.objects.exists()


def test_an_edit_publishes_the_draft_and_notifies(vendor: Vendor, owner: User) -> None:
    _operator(support_email="help@platform.example")
    VendorTranslationFactory.create(vendor=vendor, language="it", tagline="Bozza", status="machine")
    save_storefront_profile(vendor, "it", {"tagline": "Nuovo"}, actor=owner, ctx=CTX)
    assert VendorTranslation.objects.get(vendor=vendor, language="it").status == "published"
    assert OutboxEmail.objects.count() == 1


def test_a_source_language_edit_that_stales_other_languages_is_audited(
    vendor: Vendor, owner: User
) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Hi", status="published")
    it = VendorTranslationFactory.create(
        vendor=vendor, language="it", tagline="Ciao", status="published", source_hash="old"
    )
    save_storefront_profile(vendor, "en", {"tagline": "Hello"}, actor=owner, ctx=CTX)
    it.refresh_from_db()
    assert it.status == "machine"
    entry = AuditLog.objects.get(action="vendor.profile_translation_stale")
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["status"], entry.after["status"]) == ("published", "machine")
    assert entry.target_key == f"{vendor.public_id}/it"
    assert entry.vendor_key == str(vendor.public_id)


def test_no_stale_entry_when_other_languages_are_untouched(vendor: Vendor, owner: User) -> None:
    VendorTranslationFactory.create(vendor=vendor, language="it", status="machine")
    save_storefront_profile(vendor, "en", {"tagline": "Hello"}, actor=owner, ctx=CTX)
    assert not AuditLog.objects.filter(action="vendor.profile_translation_stale").exists()


def test_platform_staff_without_a_membership_cannot_edit(vendor: Vendor) -> None:
    staff = UserFactory.create(superuser=True)
    platform = UserFactory.create(permissions=["core.platform_owner"])
    for actor in (staff, platform):
        with pytest.raises(PermissionDenied):
            save_storefront_profile(vendor, "en", {"tagline": "x"}, actor=actor, ctx=CTX)


def test_an_inactive_owner_cannot_edit(vendor: Vendor, owner: User) -> None:
    owner.is_active = False
    owner.save()
    with pytest.raises(PermissionDenied):
        save_storefront_profile(vendor, "en", {"tagline": "x"}, actor=owner, ctx=CTX)


def test_staff_are_refused_on_a_dry_run_too(vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    VendorTranslationFactory.create(vendor=vendor, language="en", tagline="Secret draft")
    with pytest.raises(PermissionDenied):
        save_storefront_profile(vendor, "en", {"tagline": "x"}, actor=staff, ctx=CTX, dry_run=True)


def test_a_concurrent_first_save_gives_a_clean_error_not_a_500(
    vendor: Vendor, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = VendorTranslation.save

    def racing_save(self: VendorTranslation, *args: object, **kwargs: object) -> None:
        if self._state.adding:  # another request inserted the row first
            VendorTranslation.objects.bulk_create(
                [VendorTranslation(vendor=self.vendor, language=self.language)]
            )
        original(self, *args, **kwargs)

    monkeypatch.setattr(VendorTranslation, "save", racing_save)
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "it", {"tagline": "x"}, actor=owner, ctx=CTX)


def test_the_moderator_email_renders_hostile_values_inert() -> None:
    from apps.core import services as core_services

    rendered = core_services.render_email(
        "vendors.profile_changed",
        "en",
        {"vendor_name": "Evil <script>x()</script>", "language": "en", "fields": "<b>tagline</b>"},
    )
    assert "<script>" not in rendered.html_body
    assert "&lt;script&gt;" in rendered.html_body
    assert "<b>" not in rendered.html_body
    assert "<img" not in rendered.html_body


# --- Re-check (#204): control characters, per-vendor limit


@pytest.mark.parametrize("char", ["\x00", "\x07", "\x1b", "\x7f"])
@pytest.mark.parametrize("field", ["tagline", "description", "refund_policy"])
def test_control_characters_are_refused_not_a_500(
    vendor: Vendor, owner: User, field: str, char: str
) -> None:
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "en", {field: f"a{char}b"}, actor=owner, ctx=CTX)
    assert not VendorTranslation.objects.filter(vendor=vendor).exists()


def test_line_breaks_and_tabs_are_allowed_in_the_texts(vendor: Vendor, owner: User) -> None:
    text = "Line one\r\nLine two\n\tindented"
    save_storefront_profile(vendor, "en", {"refund_policy": text}, actor=owner, ctx=CTX)
    assert VendorTranslation.objects.get(vendor=vendor, language="en").refund_policy == text


def test_several_owners_share_the_vendors_limit(
    vendor: Vendor, owner: User, settings: object
) -> None:
    settings.VENDOR_PROFILE_SAVE_LIMIT = 100  # type: ignore[attr-defined]
    settings.VENDOR_PROFILE_VENDOR_SAVE_LIMIT = 2  # type: ignore[attr-defined]
    second = VendorMemberFactory.create(vendor=vendor, role="owner").user
    save_storefront_profile(vendor, "en", {"tagline": "a"}, actor=owner, ctx=CTX)
    save_storefront_profile(vendor, "en", {"tagline": "b"}, actor=second, ctx=CTX)
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "en", {"tagline": "c"}, actor=owner, ctx=CTX)
    with pytest.raises(ValidationError):
        save_storefront_profile(vendor, "en", {"tagline": "c"}, actor=second, ctx=CTX)


def test_the_vendor_limit_is_per_vendor(owner: User, vendor: Vendor, settings: object) -> None:
    settings.VENDOR_PROFILE_VENDOR_SAVE_LIMIT = 1  # type: ignore[attr-defined]
    other = VendorFactory.create()
    VendorMemberFactory.create(vendor=other, user=owner, role="owner")
    save_storefront_profile(vendor, "en", {"tagline": "a"}, actor=owner, ctx=CTX)
    save_storefront_profile(other, "en", {"tagline": "a"}, actor=owner, ctx=CTX)


def test_the_policy_headings_are_translated_in_italian(vendor: Vendor) -> None:
    """The Manager help texts quote these headings, so the Italian shop must show them."""
    from django.utils import translation

    from apps.core.models import TranslationStatus
    from apps.vendors.services import storefront_profile

    VendorTranslationFactory.create(
        vendor=vendor,
        language="en",
        status=TranslationStatus.PUBLISHED,
        shipping_policy="Ships in 3 days",
        refund_policy="Refunds in 14 days",
        cancellation_policy="Cancel before shipping",
    )
    with translation.override("it"):
        profile = storefront_profile(vendor, "en")
    assert profile is not None
    assert [title for title, _body in profile.policies] == [
        "Spedizione",
        "Resi e rimborsi",
        "Annullamento",
    ]
