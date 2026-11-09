"""CORE.8 (part 2): the unlock service and the unlock after a completed password reset (Q-18).

Only platform staff unlock an account; the unlock is audited; a password reset clears the
lockout only once it is completed; nothing here tells a caller whether an account is locked.
There is no "request a reset" flow in the code base yet: when one lands it must not call
`unlock_account` or `complete_password_reset` (a request proves nothing).
"""

import datetime as dt
import secrets

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, Throttle
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

IP = "203.0.113.7"
CTX = AuditContext(actor_label="staff:support", source=AuditLog.Source.ADMIN, ip=IP)


def _platform_staff() -> User:
    """A user holding `vendors.view_all_vendors` directly (the platform staff rule)."""
    permission, _ = Permission.objects.get_or_create(
        content_type=ContentType.objects.get_for_model(Vendor),
        codename="view_all_vendors",
        defaults={"name": "Can view all vendors"},
    )
    user = UserFactory.create(staff=True)
    user.user_permissions.add(permission)
    return User.objects.get(pk=user.pk)


def _locked_user() -> User:
    user = UserFactory.create()
    now = timezone.now()
    for _ in range(services.ACCOUNT_LOCKOUT.free_attempts + 1):
        services.record_failed_login(user.email, IP, now=now)
    assert services.login_retry_after(user.email, None)
    return user


def _account_rows() -> int:
    return Throttle.objects.filter(key__startswith="login:account:").count()


def test_platform_staff_unlocks_a_locked_account() -> None:
    user = _locked_user()
    changeset = services.unlock_account(user, actor=_platform_staff(), ctx=CTX)
    assert not changeset.is_empty
    assert services.login_retry_after(user.email, None) == dt.timedelta(0)
    assert _account_rows() == 0


def test_the_unlock_is_audited() -> None:
    user = _locked_user()
    services.unlock_account(user, actor=_platform_staff(), ctx=CTX)
    entry = AuditLog.objects.get(action=services.ACCOUNT_UNLOCKED)
    assert entry.target_model == "core.throttle"
    assert entry.before["failures"] > 0
    assert entry.after["failures"] == 0
    assert entry.after["locked_until"] is None
    assert entry.actor_label == "staff:support"


def test_dry_run_changes_and_audits_nothing() -> None:
    user = _locked_user()
    changeset = services.unlock_account(user, actor=_platform_staff(), ctx=CTX, dry_run=True)
    assert not changeset.is_empty
    assert services.login_retry_after(user.email, None)
    assert not AuditLog.objects.filter(action=services.ACCOUNT_UNLOCKED).exists()


def test_unlocking_an_unlocked_account_is_a_no_op() -> None:
    user = UserFactory.create()
    changeset = services.unlock_account(user, actor=_platform_staff(), ctx=CTX)
    assert changeset.is_empty
    assert not AuditLog.objects.filter(action=services.ACCOUNT_UNLOCKED).exists()


def test_the_ip_lock_stays() -> None:
    user = _locked_user()
    other = UserFactory.create()
    now = timezone.now()
    for _ in range(services.IP_LOCKOUT.free_attempts + 1):
        services.record_failed_login(other.email, IP, now=now)
    services.unlock_account(user, actor=_platform_staff(), ctx=CTX)
    assert services.login_retry_after(user.email, IP)


@pytest.mark.parametrize("kind", ["customer", "editor", "user_admin", "vendor_staff", "inactive"])
def test_everyone_but_platform_staff_is_refused(kind: str) -> None:
    user = _locked_user()
    if kind == "customer":
        actor = UserFactory.create()
    elif kind == "editor":
        actor = UserFactory.create(staff=True)
    elif kind == "user_admin":
        actor = UserFactory.create(staff=True, permissions=["accounts.change_user"])
    elif kind == "vendor_staff":
        actor = UserFactory.create()
        VendorMemberFactory.create(user=actor, vendor=VendorFactory.create())
    else:
        actor = _platform_staff()
        actor.is_active = False
        actor.save(update_fields=["is_active"])
    with pytest.raises(PermissionDenied):
        services.unlock_account(user, actor=actor, ctx=CTX)
    assert services.login_retry_after(user.email, None)
    assert not AuditLog.objects.filter(action=services.ACCOUNT_UNLOCKED).exists()


def test_a_dry_run_is_refused_too() -> None:
    with pytest.raises(PermissionDenied):
        services.unlock_account(_locked_user(), actor=UserFactory.create(), ctx=CTX, dry_run=True)


def test_a_refusal_does_not_reveal_whether_the_account_is_locked() -> None:
    actor = UserFactory.create()
    messages = set()
    for target in (_locked_user(), UserFactory.create()):
        with pytest.raises(PermissionDenied) as raised:
            services.unlock_account(target, actor=actor, ctx=CTX)
        messages.add(str(raised.value))
    assert len(messages) == 1


def test_a_completed_reset_clears_the_lockout_and_audits_both_changes() -> None:
    user = _locked_user()
    new_password = secrets.token_urlsafe(20)
    services.complete_password_reset(user, new_password, ctx=CTX)
    user.refresh_from_db()
    assert user.check_password(new_password)
    assert services.login_retry_after(user.email, None) == dt.timedelta(0)
    assert AuditLog.objects.filter(action=services.CREDENTIAL_CHANGED).count() == 1
    assert AuditLog.objects.get(action=services.ACCOUNT_UNLOCKED).target_model == "core.throttle"


def test_a_completed_reset_of_an_unlocked_account_audits_only_the_password() -> None:
    user = UserFactory.create()
    services.complete_password_reset(user, secrets.token_urlsafe(20), ctx=CTX)
    assert AuditLog.objects.filter(action=services.CREDENTIAL_CHANGED).count() == 1
    assert not AuditLog.objects.filter(action=services.ACCOUNT_UNLOCKED).exists()


def test_a_refused_new_password_changes_nothing_and_keeps_the_lock() -> None:
    user = _locked_user()
    old_hash = user.password
    with pytest.raises(ValidationError):
        services.complete_password_reset(user, "short", ctx=CTX)
    user.refresh_from_db()
    assert user.password == old_hash
    assert services.login_retry_after(user.email, None)
    assert not AuditLog.objects.filter(action=services.ACCOUNT_UNLOCKED).exists()


def test_a_reset_dry_run_changes_nothing() -> None:
    user = _locked_user()
    old_hash = user.password
    services.complete_password_reset(user, secrets.token_urlsafe(20), ctx=CTX, dry_run=True)
    user.refresh_from_db()
    assert user.password == old_hash
    assert services.login_retry_after(user.email, None)
    assert (
        not AuditLog.objects.exists()
        or not AuditLog.objects.filter(
            action__in=[services.ACCOUNT_UNLOCKED, services.CREDENTIAL_CHANGED]
        ).exists()
    )
