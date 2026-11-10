"""CORE.23: `manage.py bootstrap_platform_owner`, the first platform owner after a deploy.

Lives with the Manager tests because the user model is in accounts, which core tests may not import.
"""

import secrets
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.core.services import is_platform_owner
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db


def _run(*args: str) -> str:
    out = StringIO()
    call_command("bootstrap_platform_owner", *args, stdout=out)
    return out.getvalue()


def test_it_grants_the_permission_and_audits_it() -> None:
    user = UserFactory.create()
    assert not is_platform_owner(user)
    _run(user.email)
    assert is_platform_owner(User.objects.get(pk=user.pk))
    entry = AuditLog.objects.get(target_key=user.email)
    assert entry.source == AuditLog.Source.COMMAND
    assert entry.action == "core.platform_owner.grant"


def test_dry_run_changes_nothing_and_writes_no_audit_entry() -> None:
    user = UserFactory.create()
    out = _run(user.email, "--dry-run")
    assert "would" in out.lower()
    assert not is_platform_owner(User.objects.get(pk=user.pk))
    assert not AuditLog.objects.exists()


def test_it_is_idempotent() -> None:
    user = UserFactory.create()
    _run(user.email)
    out = _run(user.email)
    assert "already" in out.lower()
    assert AuditLog.objects.filter(target_key=user.email).count() == 1


def test_email_matching_ignores_case() -> None:
    user = UserFactory.create(email="Boss@Example.com")
    _run("boss@example.com")
    assert is_platform_owner(User.objects.get(pk=user.pk))


def test_an_unknown_user_is_refused_without_create() -> None:
    with pytest.raises(CommandError):
        _run("nobody@example.com")
    assert not User.objects.filter(email="nobody@example.com").exists()


def test_create_makes_an_active_user_without_a_usable_password() -> None:
    _run("new.owner@example.com", "--create")
    user = User.objects.get(email="new.owner@example.com")
    assert user.is_active
    assert not user.has_usable_password()
    assert is_platform_owner(user)
    assert AuditLog.objects.filter(target_key="new.owner@example.com").count() >= 1


def test_an_inactive_user_is_refused() -> None:
    user = UserFactory.create(is_active=False)
    with pytest.raises(CommandError):
        _run(user.email)
    assert not is_platform_owner(User.objects.get(pk=user.pk))


def test_the_audit_target_is_the_lower_cased_email_when_creating() -> None:
    _run("Mixed.Case@Example.com", "--create")
    assert AuditLog.objects.filter(target_key="mixed.case@example.com").exists()
    assert not AuditLog.objects.filter(target_key="Mixed.Case@Example.com").exists()


def test_a_vendor_member_is_refused_and_told_why() -> None:
    member = VendorMemberFactory.create().user
    with pytest.raises(CommandError, match="vendor member"):
        _run(member.email)
    assert not is_platform_owner(User.objects.get(pk=member.pk))
    assert not AuditLog.objects.filter(action="core.platform_owner.grant").exists()


def test_an_inactive_user_is_refused_even_with_create() -> None:
    user = UserFactory.create(is_active=False)
    with pytest.raises(CommandError):
        _run(user.email, "--create")
    assert not is_platform_owner(User.objects.get(pk=user.pk))


def test_a_second_owner_is_allowed() -> None:
    first, second = UserFactory.create(), UserFactory.create()
    _run(first.email)
    _run(second.email)
    assert is_platform_owner(User.objects.get(pk=second.pk))


def test_a_created_owner_prints_no_secret_and_is_sent_to_mfa_enrolment(client: Client) -> None:
    out = _run("fresh.owner@example.com", "--create")
    assert "no usable password" in out
    user = User.objects.get(email="fresh.owner@example.com")
    assert user.password not in out
    # Once a password is set (CORE.19 or the staff admin), the gate forces enrolment first.
    user.set_password(secrets.token_urlsafe(20))
    user.save()
    client.force_login(user)
    response = client.get("/manager/platform/")
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:setup"))


def test_a_forged_vendor_choice_and_unknown_paths_stay_404(client: Client, owner: User) -> None:
    verified_login(client, owner)
    assert client.post("/manager/shop/vendor/", {"vendor": "forged"}).status_code == 404
    assert client.get("/manager/shop/nowhere/").status_code == 404
