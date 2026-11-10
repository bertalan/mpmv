"""CORE.23: `manage.py bootstrap_platform_owner`, the first platform owner after a deploy."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog
from apps.core.services import is_platform_owner

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
