"""CORE.18 2/2: the role assignment service and the "Staff and roles" page (spec 02).

Done-when items covered here: incompatible pairs, last owner, step-up and AuditLog on grant and
revocation, platform roles and vendor membership mutually exclusive, only the platform owner
assigns (404 for everyone else), the owner's self-assignment with a reason and an email, the
one-person-team setting and its permanent warning.
"""

import json

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import Client

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.manager import roles, staff_roles
from apps.manager.role_groups import user_roles
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db
CTX = AuditContext(actor_label="staff:test", source=AuditLog.Source.ADMIN)
URL = "/manager/platform/staff/"


def staff(key: str | None = None, **kwargs: object) -> User:
    return UserFactory.create(platform_role=key, **kwargs)


def held(user: User) -> frozenset[str]:
    return user_roles(User.objects.get(pk=user.pk))


def test_a_grant_adds_the_group_sets_is_staff_and_is_audited_with_before_and_after() -> None:
    owner, target = staff("owner"), UserFactory.create()
    changes = staff_roles.grant_role(target, "support", actor=owner, ctx=CTX)
    target.refresh_from_db()
    assert held(target) == {"support"}
    assert target.is_staff
    assert not changes.is_empty
    entry = AuditLog.objects.get(action=staff_roles.GRANT_ACTION)
    assert entry.before["roles"] == []
    assert entry.after["roles"] == ["support"]


def test_a_dry_run_changes_nothing_and_returns_the_change() -> None:
    owner, target = staff("owner"), UserFactory.create()
    changes = staff_roles.grant_role(target, "support", actor=owner, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    target.refresh_from_db()
    assert held(target) == frozenset()
    assert not target.is_staff
    assert not AuditLog.objects.filter(action=staff_roles.GRANT_ACTION).exists()


def test_granting_a_role_already_held_is_a_no_op() -> None:
    owner, target = staff("owner"), staff("support")
    assert staff_roles.grant_role(target, "support", actor=owner, ctx=CTX).is_empty


@pytest.mark.parametrize("actor_key", [None, "config", "catalog", "finance", "support"])
def test_only_the_platform_owner_assigns(actor_key: str | None) -> None:
    actor, target = staff(actor_key), UserFactory.create()
    with pytest.raises(PermissionDenied):
        staff_roles.grant_role(target, "support", actor=actor, ctx=CTX)
    assert held(target) == frozenset()


def test_a_superuser_without_a_role_does_not_assign() -> None:
    target = UserFactory.create()
    with pytest.raises(PermissionDenied):
        staff_roles.grant_role(target, "support", actor=UserFactory.create(superuser=True), ctx=CTX)


def test_a_vendor_member_is_refused() -> None:
    member = VendorMemberFactory.create().user
    with pytest.raises(staff_roles.RoleAssignmentError):
        staff_roles.grant_role(member, "support", actor=staff("owner"), ctx=CTX)
    assert held(member) == frozenset()


def test_an_inactive_account_is_refused() -> None:
    with pytest.raises(staff_roles.RoleAssignmentError):
        staff_roles.grant_role(
            UserFactory.create(is_active=False), "support", actor=staff("owner"), ctx=CTX
        )


def test_finance_and_config_together_are_refused_unless_one_person_team(settings: object) -> None:
    owner, target = staff("owner"), staff("finance")
    with pytest.raises(staff_roles.RoleAssignmentError, match="one-person-team"):
        staff_roles.grant_role(target, "config", actor=owner, ctx=CTX)
    assert held(target) == {"finance"}
    settings.FEATURE_FLAGS = {"one_person_team": True}  # type: ignore[attr-defined]
    staff_roles.grant_role(target, "config", actor=owner, ctx=CTX)
    assert held(target) == {"finance", "config"}


def test_the_owner_cannot_give_themselves_the_incompatible_second_role() -> None:
    owner = staff("owner")
    staff_roles.grant_role(owner, "finance", actor=owner, ctx=CTX, reason="cover")
    with pytest.raises(staff_roles.RoleAssignmentError):
        staff_roles.grant_role(owner, "config", actor=owner, ctx=CTX, reason="cover")


def test_self_assignment_needs_a_reason_and_emails_the_other_owners_and_superusers() -> None:
    owner, other_owner = staff("owner"), staff("owner")
    superuser = UserFactory.create(superuser=True)
    staff("support")  # not a recipient
    with pytest.raises(staff_roles.RoleAssignmentError, match="reason"):
        staff_roles.grant_role(owner, "support", actor=owner, ctx=CTX)
    assert held(owner) == {"owner"}
    staff_roles.grant_role(owner, "support", actor=owner, ctx=CTX, reason="cover leave")
    assert held(owner) == {"owner", "support"}
    entry = AuditLog.objects.get(action=staff_roles.GRANT_ACTION)
    assert entry.after["reason"] == "cover leave"
    recipients = set(OutboxEmail.objects.values_list("recipient", flat=True))
    assert recipients == {other_owner.email, superuser.email}


def test_a_revocation_is_audited_and_clears_is_staff_with_the_last_role() -> None:
    owner, target = staff("owner"), staff("support")
    staff_roles.revoke_role(target, "support", actor=owner, ctx=CTX)
    target.refresh_from_db()
    assert held(target) == frozenset()
    assert not target.is_staff
    entry = AuditLog.objects.get(action=staff_roles.REVOKE_ACTION)
    assert entry.before["roles"] == ["support"]
    assert entry.after["roles"] == []


def test_a_revocation_keeps_is_staff_while_another_role_remains() -> None:
    owner, target = staff("owner"), staff("support")
    staff_roles.grant_role(target, "catalog", actor=owner, ctx=CTX)
    staff_roles.revoke_role(target, "support", actor=owner, ctx=CTX)
    target.refresh_from_db()
    assert held(target) == {"catalog"}
    assert target.is_staff


def test_the_last_platform_owner_is_never_removed() -> None:
    owner = staff("owner")
    with pytest.raises(staff_roles.RoleAssignmentError, match="last platform owner"):
        staff_roles.revoke_role(owner, "owner", actor=owner, ctx=CTX)
    assert held(owner) == {"owner"}
    second = staff("owner")
    staff_roles.revoke_role(second, "owner", actor=owner, ctx=CTX)
    assert held(second) == frozenset()


def test_a_revocation_dry_run_changes_nothing() -> None:
    owner, target = staff("owner"), staff("support")
    changes = staff_roles.revoke_role(target, "support", actor=owner, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert held(target) == {"support"}


# --- The page


def login(client: Client, user: User, **kwargs: object) -> None:
    verified_login(client, user, **kwargs)  # type: ignore[arg-type]


def grant_post(email: str, role: str, reason: str = "") -> dict[str, str]:
    return {"action": "grant", "email": email, "role": role, "reason": reason}


def test_the_owner_sees_the_staff_list(client: Client) -> None:
    owner, support = staff("owner"), staff("support", full_name="Sam Support")
    login(client, owner)
    page = client.get(URL)
    assert page.status_code == 200
    assert support.email in page.text
    assert 'href="/manager/platform/staff/"' in page.text


@pytest.mark.parametrize("key", [None, "config", "catalog", "finance", "support"])
def test_everyone_but_the_owner_gets_404_on_get_and_post_and_nothing_changes(
    client: Client, key: str | None
) -> None:
    actor, target = staff(key), UserFactory.create()
    login(client, actor)
    assert client.get(URL).status_code == 404
    assert client.post(URL, grant_post(target.email, "support")).status_code == 404
    assert held(target) == frozenset()


def test_a_vendor_member_and_a_superuser_without_a_role_get_404(client: Client) -> None:
    for user in (VendorMemberFactory.create().user, UserFactory.create(superuser=True)):
        login(client, user)
        assert client.get(URL).status_code == 404
        assert client.post(URL, grant_post("x@example.com", "support")).status_code == 404


def test_a_grant_through_the_page(client: Client) -> None:
    owner, target = staff("owner"), UserFactory.create()
    login(client, owner)
    response = client.post(URL, grant_post(target.email, "support"))
    assert response.status_code == 302
    assert held(target) == {"support"}


def test_a_grant_without_operational_mode_is_refused_with_an_explanation(client: Client) -> None:
    owner, target = staff("owner"), UserFactory.create()
    login(client, owner, level=levels.RESTRICTED)
    response = client.post(URL, grant_post(target.email, "support"))
    assert response.status_code == 403
    assert held(target) == frozenset()
    assert AuditLog.objects.filter(action=staff_roles.GRANT_ACTION).count() == 0


def test_a_grant_without_a_recent_step_up_is_refused(client: Client) -> None:
    owner, target = staff("owner"), UserFactory.create()
    login(client, owner, verified_at=1.0)
    response = client.post(URL, grant_post(target.email, "support"))
    assert response.status_code in (302, 403)
    assert held(target) == frozenset()


def test_the_owner_role_needs_a_fresh_passkey(client: Client) -> None:
    owner, target = staff("owner"), UserFactory.create()
    login(client, owner)  # operational, but no passkey
    response = client.post(URL, grant_post(target.email, "owner"))
    assert response.status_code == 302
    assert "/mfa/" in response["Location"]
    assert held(target) == frozenset()
    login(client, owner, passkey=True)
    assert client.post(URL, grant_post(target.email, "owner")).status_code == 302
    assert held(target) == {"owner"}


def test_a_revocation_through_the_page_needs_the_same_step_up(client: Client) -> None:
    owner, target = staff("owner"), staff("support")
    login(client, owner, level=levels.RESTRICTED)
    post = {"action": "revoke", "email": target.email, "role": "support", "reason": ""}
    assert client.post(URL, post).status_code == 403
    assert held(target) == {"support"}
    login(client, owner)
    assert client.post(URL, post).status_code == 302
    assert held(target) == frozenset()


def test_a_refusal_is_shown_on_the_page_and_changes_nothing(client: Client) -> None:
    owner, target = staff("owner"), staff("finance")
    login(client, owner)
    response = client.post(URL, grant_post(target.email, "config"))
    assert response.status_code == 200
    assert "one-person-team" in response.text
    assert held(target) == {"finance"}


def test_an_unknown_email_or_role_changes_nothing(client: Client) -> None:
    owner = staff("owner")
    login(client, owner)
    assert client.post(URL, grant_post("nobody@example.com", "support")).status_code == 200
    assert client.post(URL, grant_post(owner.email, "wizard")).status_code == 200
    assert AuditLog.objects.filter(action=staff_roles.GRANT_ACTION).count() == 0


def test_the_one_person_team_warning_is_permanent_while_the_setting_is_on(
    client: Client, settings: object
) -> None:
    login(client, staff("owner"))
    assert "one-person-team" not in client.get(URL).text.lower()
    settings.FEATURE_FLAGS = {"one_person_team": True}  # type: ignore[attr-defined]
    assert "one-person-team" in client.get(URL).text.lower()


def test_the_audit_entry_of_a_grant_carries_no_email_address(client: Client) -> None:
    owner, target = staff("owner"), UserFactory.create()
    login(client, owner)
    client.post(URL, grant_post(target.email, "support"))
    entry = AuditLog.objects.get(action=staff_roles.GRANT_ACTION)
    assert target.email not in json.dumps([entry.before, entry.after, entry.actor_label])
    assert Group.objects.get(name=roles.role("support").group).user_set.filter(pk=target.pk)
