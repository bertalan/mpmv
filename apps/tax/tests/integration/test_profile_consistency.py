"""TAX.2: the re-check TAX.1 deferred. The vendor's declared fiscal regime (VEN.12) can change
outside the regime change workflow (vendor admin, imports); vendors cannot import tax, so the
tax app finds current profiles that no longer agree with it and flags them: a warning in the
tax admin and a nightly job that logs each one."""

import logging
from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.tasks import schedules
from apps.core.tests.factories import utc
from apps.tax import services, tasks
from apps.tax.tests.factories import VendorTaxProfileFactory
from apps.vendors.engine import FLAT_RATE_REGIME
from apps.vendors.models import Vendor

pytestmark = pytest.mark.django_db

MfaLogin = Callable[[Client, User], Client]


def drifted_vendor() -> Vendor:
    """A domestic-only vendor whose fiscal regime was later set to RF19 in the vendor admin."""
    profile = VendorTaxProfileFactory.create(vendor__fiscal_regime="RF01", valid_from=utc(2025))
    Vendor.objects.filter(pk=profile.vendor.pk).update(fiscal_regime=FLAT_RATE_REGIME)
    return profile.vendor


def test_current_profiles_that_disagree_with_the_fiscal_regime_are_found() -> None:
    drifted = drifted_vendor()
    VendorTaxProfileFactory.create(vendor__fiscal_regime="RF01", valid_from=utc(2025))
    VendorTaxProfileFactory.create(flat_rate=True, valid_from=utc(2025))

    found = services.inconsistent_tax_profiles()
    assert [item.profile.vendor_id for item in found] == [drifted.pk]
    assert "regime" in found[0].errors


def test_closed_and_future_profiles_are_not_checked() -> None:
    closed = VendorTaxProfileFactory.create(
        vendor__fiscal_regime=FLAT_RATE_REGIME, valid_from=utc(2024), valid_to=utc(2025)
    )
    future = VendorTaxProfileFactory.create(
        vendor__fiscal_regime=FLAT_RATE_REGIME, valid_from=utc(2099)
    )
    assert closed.vendor_id != future.vendor_id
    assert services.inconsistent_tax_profiles() == []


def test_the_nightly_job_logs_each_inconsistent_profile(caplog: pytest.LogCaptureFixture) -> None:
    drifted = drifted_vendor()
    with caplog.at_level(logging.WARNING, logger="apps.tax"):
        tasks.check_profile_consistency.func(timestamp=0)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(drifted.public_id) in warnings[0].getMessage()


def test_the_consistency_job_runs_every_night() -> None:
    names = {schedule.task_name for schedule in schedules()}
    assert "tax.check_profile_consistency" in names


def test_the_tax_admin_warns_about_inconsistent_profiles(
    client: Client, superuser: User, mfa_login: MfaLogin
) -> None:
    drifted = drifted_vendor()
    staff_client = mfa_login(client, superuser)
    for name in ("vendortaxprofile", "taxregimechangerequest"):
        response = staff_client.get(reverse(f"admin:tax_{name}_changelist"))
        assert response.status_code == 200
        content = response.content.decode()
        assert f"{drifted}: the tax profile does not match the fiscal regime" in content
