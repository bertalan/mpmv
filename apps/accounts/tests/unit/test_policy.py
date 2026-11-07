"""CORE.4: who must use MFA (`mfa_required`) and the configuration check. No database."""

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.accounts.checks import check_mfa_middleware
from apps.accounts.services import mfa_required
from apps.accounts.tests.factories import UserFactory

THIS_MODULE = "apps.accounts.tests.unit.test_policy"


def everyone(user: Any) -> bool:
    return True


def nobody(user: Any) -> bool:
    return False


def test_staff_must_use_mfa() -> None:
    assert mfa_required(UserFactory.build(staff=True)) is True


def test_superusers_must_use_mfa() -> None:
    assert mfa_required(UserFactory.build(superuser=True)) is True


def test_customers_do_not_need_mfa_without_extra_policies(settings: SettingsWrapper) -> None:
    # The installed vendor-owner policy reads the database: tested in
    # apps/vendors/tests/integration/test_owner_mfa.py.
    settings.MFA_REQUIRED_POLICIES = []
    assert mfa_required(UserFactory.build()) is False


def test_anonymous_visitors_never_need_mfa() -> None:
    assert mfa_required(AnonymousUser()) is False


def test_extra_policies_from_settings_extend_the_rule(settings: SettingsWrapper) -> None:
    """CORE.2 adds vendor owners by listing a check in settings.MFA_REQUIRED_POLICIES."""
    settings.MFA_REQUIRED_POLICIES = [f"{THIS_MODULE}.nobody", f"{THIS_MODULE}.everyone"]
    assert mfa_required(UserFactory.build()) is True


def test_extra_policies_cannot_exempt_staff(settings: SettingsWrapper) -> None:
    settings.MFA_REQUIRED_POLICIES = [f"{THIS_MODULE}.nobody"]
    assert mfa_required(UserFactory.build(staff=True)) is True


def test_the_project_configuration_passes_the_mfa_check() -> None:
    assert check_mfa_middleware(None) == []


@pytest.mark.parametrize(
    "missing",
    ["django_otp.middleware.OTPMiddleware", "apps.accounts.middleware.MfaRequiredMiddleware"],
)
def test_the_check_fails_when_an_mfa_middleware_is_missing(
    settings: SettingsWrapper, missing: str
) -> None:
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if m != missing]
    errors = check_mfa_middleware(None)
    assert [e.id for e in errors] == ["accounts.E002"]


def test_the_check_fails_when_mfa_runs_before_authentication(settings: SettingsWrapper) -> None:
    mfa = "apps.accounts.middleware.MfaRequiredMiddleware"
    settings.MIDDLEWARE = [mfa, *(m for m in settings.MIDDLEWARE if m != mfa)]
    assert [e.id for e in check_mfa_middleware(None)] == ["accounts.E002"]
