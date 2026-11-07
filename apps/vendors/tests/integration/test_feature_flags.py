"""ARC.8: per-vendor feature flag overrides (vendor-owned rows, CORE.3) and their admin."""

import time
from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http.response import HttpResponseBase
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core import flags
from apps.core.audit import AuditContext
from apps.core.models import VENDOR_OVERRIDES, AuditLog, FeatureFlag
from apps.core.services import flag_enabled, save_flag
from apps.core.tests.factories import FeatureFlagFactory
from apps.core.tests.flags import registered
from apps.vendors.models import VendorFeatureFlag
from apps.vendors.services import delete_vendor_flag, save_vendor_flag
from apps.vendors.tests.factories import VendorFactory, VendorFeatureFlagFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:ops", source="admin", ip="127.0.0.1")
FEATURE = flags.FlagSpec("test_feature", "A feature behind a flag.", default=False)


@pytest.fixture
def feature() -> Iterator[FeatureFlag]:
    with registered(FEATURE):
        yield FeatureFlagFactory.create(name=FEATURE.name)


def switch(name: str) -> FeatureFlag:
    return FeatureFlag.objects.get(name=name)


def test_core_reads_the_overrides_through_the_documented_relation() -> None:
    field = VendorFeatureFlag._meta.get_field("flag")
    assert field.remote_field.related_name == VENDOR_OVERRIDES


def test_a_vendor_override_turns_a_feature_on_for_that_vendor_only(
    feature: FeatureFlag,
) -> None:
    mine, other = VendorFactory.create(), VendorFactory.create()
    save_vendor_flag(VendorFeatureFlag(vendor=mine, flag=feature, enabled=True), ctx=CTX)

    assert flag_enabled(feature.name, vendor=mine) is True
    assert flag_enabled(feature.name, vendor=other) is False
    assert flag_enabled(feature.name) is False


def test_a_kill_switch_can_be_pulled_for_one_vendor() -> None:
    mine, other = VendorFactory.create(), VendorFactory.create()
    save_vendor_flag(
        VendorFeatureFlag(vendor=mine, flag=switch(flags.CHECKOUT), enabled=False), ctx=CTX
    )
    assert flag_enabled(flags.CHECKOUT, vendor=mine) is False
    assert flag_enabled(flags.CHECKOUT, vendor=other) is True
    assert flag_enabled(flags.CHECKOUT) is True


def test_a_vendor_override_cannot_bypass_the_platform_kill_switch() -> None:
    vendor = VendorFactory.create()
    VendorFeatureFlagFactory.create(vendor=vendor, flag=switch(flags.PAYMENTS), enabled=True)
    killed = switch(flags.PAYMENTS)
    killed.state = flags.OFF
    save_flag(killed, ctx=CTX)
    assert flag_enabled(flags.PAYMENTS, vendor=vendor) is False


def test_an_override_is_audited_under_its_vendor() -> None:
    vendor = VendorFactory.create()
    override = VendorFeatureFlag(
        vendor=vendor, flag=switch(flags.IMPORT), enabled=False, note="Broken feed"
    )
    save_vendor_flag(override, ctx=CTX)

    entry = AuditLog.objects.get(action="vendor.feature_flag_saved")
    assert entry.target_model == "vendors.vendorfeatureflag"
    assert entry.target_key == str(override.public_id)
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.before is None
    assert entry.after == {
        "vendor": str(vendor.public_id),
        "flag": "import",
        "enabled": False,
        "note": "Broken feed",
    }


def test_removing_an_override_restores_the_platform_value_and_is_audited() -> None:
    vendor = VendorFactory.create()
    override = VendorFeatureFlagFactory.create(
        vendor=vendor, flag=switch(flags.CHECKOUT), enabled=False
    )
    assert flag_enabled(flags.CHECKOUT, vendor=vendor) is False

    delete_vendor_flag(override, ctx=CTX)
    assert flag_enabled(flags.CHECKOUT, vendor=vendor) is True
    entry = AuditLog.objects.get(action="vendor.feature_flag_deleted")
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.after is None


def test_a_dry_run_writes_no_override() -> None:
    vendor = VendorFactory.create()
    override = VendorFeatureFlag(vendor=vendor, flag=switch(flags.CHECKOUT), enabled=False)
    changes = save_vendor_flag(override, ctx=CTX, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not VendorFeatureFlag.objects.exists()
    assert flag_enabled(flags.CHECKOUT, vendor=vendor) is True


def test_an_override_of_an_unregistered_flag_is_refused() -> None:
    retired = FeatureFlagFactory.create(name="retired_feature")
    override = VendorFeatureFlag(vendor=VendorFactory.create(), flag=retired, enabled=True)
    with pytest.raises(ValidationError, match="not a registered"):
        save_vendor_flag(override, ctx=CTX)


def test_one_override_per_vendor_and_flag() -> None:
    existing = VendorFeatureFlagFactory.create()
    with pytest.raises(IntegrityError):
        VendorFeatureFlagFactory.create(vendor=existing.vendor, flag=existing.flag)


# --- Admin


@pytest.fixture
def staff() -> User:
    return UserFactory.create(email="ops@example.com", superuser=True)


@pytest.fixture
def staff_client(client: Client, staff: User) -> Client:
    verified_login(client, staff)
    return client


@pytest.fixture
def stale_client(client: Client, staff: User) -> Client:
    """Staff whose last second-factor verification is an hour old."""
    verified_login(client, staff, verified_at=time.time() - 3600)
    return client


def asks_for_step_up(response: HttpResponseBase) -> bool:
    if response.status_code != 302:
        return False
    return urlsplit(response["Location"]).path == reverse("mfa:verify")


def test_staff_pull_a_kill_switch_for_one_vendor_from_the_admin(staff_client: Client) -> None:
    vendor = VendorFactory.create()
    response = staff_client.post(
        reverse("admin:vendors_vendorfeatureflag_add"),
        {
            "vendor": vendor.pk,
            "flag": switch(flags.CHECKOUT).pk,
            "enabled": "",
            "note": "Stock sync broken",
        },
    )
    assert response.status_code == 302, response.content
    assert flag_enabled(flags.CHECKOUT, vendor=vendor) is False
    entry = AuditLog.objects.get(action="vendor.feature_flag_saved")
    assert entry.actor_label == "staff:ops@example.com"
    assert entry.vendor_key == str(vendor.public_id)


def test_staff_change_and_remove_an_override_from_the_admin(staff_client: Client) -> None:
    override = VendorFeatureFlagFactory.create(flag=switch(flags.IMPORT), enabled=False)
    change = reverse("admin:vendors_vendorfeatureflag_change", args=[override.pk])
    staff_client.post(
        change,
        {"vendor": override.vendor.pk, "flag": override.flag.pk, "enabled": "on", "note": ""},
    )
    assert VendorFeatureFlag.objects.get(pk=override.pk).enabled is True

    delete = reverse("admin:vendors_vendorfeatureflag_delete", args=[override.pk])
    staff_client.post(delete, {"post": "yes"})
    assert not VendorFeatureFlag.objects.filter(pk=override.pk).exists()
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"vendor.feature_flag_saved", "vendor.feature_flag_deleted"} <= actions


def test_the_bulk_delete_action_is_not_offered(staff_client: Client) -> None:
    VendorFeatureFlagFactory.create()
    response = staff_client.get(reverse("admin:vendors_vendorfeatureflag_changelist"))
    assert response.status_code == 200
    assert "delete_selected" not in response.content.decode()


def test_overrides_of_kill_switches_ask_for_a_fresh_second_factor(stale_client: Client) -> None:
    override = VendorFeatureFlagFactory.create(flag=switch(flags.PAYMENTS), enabled=False)
    for url in (
        reverse("admin:vendors_vendorfeatureflag_add"),
        reverse("admin:vendors_vendorfeatureflag_change", args=[override.pk]),
        reverse("admin:vendors_vendorfeatureflag_delete", args=[override.pk]),
    ):
        assert asks_for_step_up(stale_client.get(url)), url
    delete = reverse("admin:vendors_vendorfeatureflag_delete", args=[override.pk])
    assert asks_for_step_up(stale_client.post(delete, {"post": "yes"}))
    assert VendorFeatureFlag.objects.filter(pk=override.pk).exists()


def test_an_override_of_an_ordinary_flag_needs_no_step_up(
    stale_client: Client, feature: FeatureFlag
) -> None:
    override = VendorFeatureFlagFactory.create(flag=feature)
    change = reverse("admin:vendors_vendorfeatureflag_change", args=[override.pk])
    assert stale_client.get(change).status_code == 200


def test_an_ordinary_override_cannot_be_turned_into_a_kill_switch_without_step_up(
    stale_client: Client, feature: FeatureFlag
) -> None:
    """The change page skips step-up for ordinary flags, so it must not let the flag (or the
    vendor) be swapped: the identity of an override is fixed once it exists."""
    override = VendorFeatureFlagFactory.create(flag=feature, enabled=True)
    change = reverse("admin:vendors_vendorfeatureflag_change", args=[override.pk])
    other = VendorFactory.create()
    response = stale_client.post(
        change,
        {"vendor": other.pk, "flag": switch(flags.CHECKOUT).pk, "enabled": "", "note": ""},
    )

    assert response.status_code == 302, response.content
    saved = VendorFeatureFlag.objects.get(pk=override.pk)
    assert saved.flag_id == feature.pk
    assert saved.vendor_id == override.vendor_id
    assert flag_enabled(flags.CHECKOUT, vendor=override.vendor) is True
    assert flag_enabled(flags.CHECKOUT, vendor=other) is True


def test_the_kill_switch_on_the_flag_admin_asks_for_step_up(stale_client: Client) -> None:
    """The core admin uses the CORE.4 decorator named in settings (core cannot import it)."""
    url = reverse("admin:core_featureflag_change", args=[switch(flags.CHECKOUT).pk])
    assert asks_for_step_up(stale_client.get(url))
