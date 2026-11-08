"""Audit #98 chunk 3 M1: staff without superuser status cannot escalate through the admin.

The attacker in every test is a staff member who is not a superuser, holds only
`accounts.view_user` and `accounts.change_user`, and verified their second factor an hour ago
(a stale step-up). Rules (strict reading until CORE.8 replaces the Django admin):

- only a superuser grants superuser or staff status, groups or permissions;
- only a superuser sets the password of, or resets MFA for, a staff or superuser account;
- the user change form and the password form need a fresh step-up;
- the group admin is for superusers only.

Tests that check a rule other than step-up run with both a stale and a fresh verification, so
the rule holds even after the attacker steps up.
"""

import secrets
import time
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import verified_login

pytestmark = pytest.mark.django_db

STALE = time.time() - 3600

FRESHNESS = pytest.mark.parametrize("fresh", [False, True], ids=["stale", "fresh"])


@pytest.fixture
def attacker() -> User:
    return UserFactory.create(
        email="support@example.com",
        staff=True,
        permissions=["accounts.view_user", "accounts.change_user"],
    )


def login(client: Client, user: User, *, fresh: bool = False) -> None:
    verified_login(client, user, verified_at=None if fresh else STALE)


def user_form(user: User, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "email": user.email,
        "full_name": user.full_name,
        "preferred_language": user.preferred_language,
        "is_active": "on" if user.is_active else "",
        "is_staff": "on" if user.is_staff else "",
        "is_superuser": "on" if user.is_superuser else "",
        "groups": [],
        "user_permissions": [],
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value != ""}


def _permission(label: str) -> Permission:
    app_label, codename = label.split(".")
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)


def _access(user: User) -> tuple[bool, bool, set[str], set[str]]:
    user.refresh_from_db()
    return (
        user.is_staff,
        user.is_superuser,
        set(user.groups.values_list("name", flat=True)),
        {f"{p.content_type.app_label}.{p.codename}" for p in user.user_permissions.all()},
    )


# --- Only a superuser grants access


@FRESHNESS
def test_staff_cannot_make_themselves_superuser(
    client: Client, attacker: User, fresh: bool
) -> None:
    login(client, attacker, fresh=fresh)
    before = _access(attacker)
    client.post(
        reverse("admin:accounts_user_change", args=[attacker.pk]),
        user_form(
            attacker,
            is_superuser="on",
            groups=[Group.objects.create(name="Admins").pk],
            user_permissions=[_permission("auth.change_group").pk],
        ),
    )
    assert _access(attacker) == before
    assert not attacker.is_superuser


@FRESHNESS
def test_staff_cannot_grant_staff_status_groups_or_permissions(
    client: Client, attacker: User, fresh: bool
) -> None:
    customer = UserFactory.create(email="customer@example.com")
    login(client, attacker, fresh=fresh)
    client.post(
        reverse("admin:accounts_user_change", args=[customer.pk]),
        user_form(
            customer,
            is_staff="on",
            is_superuser="on",
            groups=[Group.objects.create(name="Admins").pk],
            user_permissions=[_permission("accounts.change_user").pk],
        ),
    )
    assert _access(customer) == (False, False, set(), set())


def test_the_access_fields_are_read_only_for_staff(client: Client, attacker: User) -> None:
    login(client, attacker, fresh=True)
    response = client.get(reverse("admin:accounts_user_change", args=[attacker.pk]))
    assert response.status_code == 200
    form_fields = response.context["adminform"].form.fields
    for name in ("is_superuser", "is_staff", "groups", "user_permissions"):
        assert name not in form_fields
    assert "full_name" in form_fields  # the rest of the profile stays editable


def test_staff_can_still_edit_a_profile_after_stepping_up(client: Client, attacker: User) -> None:
    customer = UserFactory.create(email="customer@example.com", full_name="Old Name")
    login(client, attacker, fresh=True)
    response = client.post(
        reverse("admin:accounts_user_change", args=[customer.pk]),
        user_form(customer, full_name="New Name"),
    )
    assert response.status_code == 302, response.content.decode()
    customer.refresh_from_db()
    assert customer.full_name == "New Name"


def test_a_superuser_still_grants_access(client: Client, superuser: User) -> None:
    customer = UserFactory.create(email="customer@example.com")
    login(client, superuser, fresh=True)
    response = client.post(
        reverse("admin:accounts_user_change", args=[customer.pk]),
        user_form(customer, is_staff="on", user_permissions=[_permission("auth.view_group").pk]),
    )
    assert response.status_code == 302, response.content.decode()
    assert _access(customer) == (True, False, set(), {"auth.view_group"})


# --- Only a superuser sets the password of a staff or superuser account


def _set_password(client: Client, target: User) -> tuple[int, str]:
    password = secrets.token_urlsafe(16)
    response = client.post(
        reverse("admin:auth_user_password_change", args=[target.pk]),
        {"usable_password": "true", "password1": password, "password2": password},
    )
    return response.status_code, password


@FRESHNESS
@pytest.mark.parametrize("role", ["superuser", "staff", "self"])
def test_staff_cannot_set_the_password_of_a_staff_account(
    client: Client, attacker: User, superuser: User, fresh: bool, role: str
) -> None:
    target = {
        "superuser": superuser,
        "staff": UserFactory.create(email="colleague@example.com", staff=True),
        "self": attacker,
    }[role]
    login(client, attacker, fresh=fresh)
    old_hash = target.password
    status, password = _set_password(client, target)
    assert status in (302, 403)
    target.refresh_from_db()
    assert target.password == old_hash
    assert not target.check_password(password)


def test_a_fresh_staff_member_refused_with_403_on_a_superuser_password(
    client: Client, attacker: User, superuser: User
) -> None:
    login(client, attacker, fresh=True)
    assert (
        client.get(reverse("admin:auth_user_password_change", args=[superuser.pk])).status_code
        == 403
    )
    assert _set_password(client, superuser)[0] == 403


def test_staff_can_still_set_a_customer_password_after_stepping_up(
    client: Client, attacker: User
) -> None:
    customer = UserFactory.create(email="customer@example.com")
    login(client, attacker, fresh=True)
    status, password = _set_password(client, customer)
    assert status == 302
    customer.refresh_from_db()
    assert customer.check_password(password)


def test_a_superuser_still_sets_a_staff_password(client: Client, superuser: User) -> None:
    colleague = UserFactory.create(email="colleague@example.com", staff=True)
    login(client, superuser, fresh=True)
    status, password = _set_password(client, colleague)
    assert status == 302
    colleague.refresh_from_db()
    assert colleague.check_password(password)


# --- Only a superuser resets MFA for a staff or superuser account


def _reset_mfa(client: Client, *targets: User) -> None:
    client.post(
        reverse("admin:accounts_user_changelist"),
        {"action": "reset_mfa", "_selected_action": [t.pk for t in targets]},
    )


@FRESHNESS
@pytest.mark.parametrize("role", ["superuser", "staff"])
def test_staff_cannot_reset_mfa_of_a_staff_account(
    client: Client, attacker: User, superuser: User, fresh: bool, role: str
) -> None:
    target = superuser if role == "superuser" else UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=target)
    customer = UserFactory.create(email="customer@example.com")
    TOTPDeviceFactory.create(user=customer)
    login(client, attacker, fresh=fresh)
    _reset_mfa(client, customer, target)
    assert services.has_second_factor(target)
    assert services.has_second_factor(customer)  # a refused batch changes nothing


def test_staff_can_still_reset_mfa_of_a_customer_after_stepping_up(
    client: Client, attacker: User
) -> None:
    customer = UserFactory.create(email="customer@example.com")
    TOTPDeviceFactory.create(user=customer)
    login(client, attacker, fresh=True)
    _reset_mfa(client, customer)
    assert not services.has_second_factor(customer)


def test_a_superuser_still_resets_mfa_of_a_staff_account(client: Client, superuser: User) -> None:
    colleague = UserFactory.create(staff=True)
    TOTPDeviceFactory.create(user=colleague)
    login(client, superuser, fresh=True)
    _reset_mfa(client, colleague)
    assert not services.has_second_factor(colleague)


# --- Step-up on the change form and the password form


def test_the_user_change_form_needs_a_fresh_step_up(client: Client, attacker: User) -> None:
    customer = UserFactory.create(email="customer@example.com", full_name="Old Name")
    login(client, attacker)
    url = reverse("admin:accounts_user_change", args=[customer.pk])
    for response in (client.get(url), client.post(url, user_form(customer, full_name="New"))):
        assert response.status_code == 302
        assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    customer.refresh_from_db()
    assert customer.full_name == "Old Name"


def test_the_password_form_needs_a_fresh_step_up(client: Client, attacker: User) -> None:
    customer = UserFactory.create(email="customer@example.com")
    login(client, attacker)
    old_hash = customer.password
    url = reverse("admin:auth_user_password_change", args=[customer.pk])
    assert urlsplit(client.get(url)["Location"]).path == reverse("mfa:verify")
    status, _ = _set_password(client, customer)
    assert status == 302
    customer.refresh_from_db()
    assert customer.password == old_hash


def test_a_superuser_also_needs_a_fresh_step_up(client: Client, superuser: User) -> None:
    customer = UserFactory.create(email="customer@example.com")
    login(client, superuser)
    for url in (
        reverse("admin:accounts_user_change", args=[customer.pk]),
        reverse("admin:auth_user_password_change", args=[customer.pk]),
    ):
        assert urlsplit(client.get(url)["Location"]).path == reverse("mfa:verify")


# --- The group admin is for superusers only


@FRESHNESS
def test_staff_cannot_use_the_group_admin_even_with_group_permissions(
    client: Client, fresh: bool
) -> None:
    group = Group.objects.create(name="Support")
    member = UserFactory.create(
        email="support@example.com",
        staff=True,
        permissions=[
            "auth.view_group",
            "auth.add_group",
            "auth.change_group",
            "auth.delete_group",
        ],
    )
    member.groups.add(group)
    login(client, member, fresh=fresh)
    escalation = _permission("accounts.change_user")
    assert client.get(reverse("admin:auth_group_changelist")).status_code == 403
    assert client.get(reverse("admin:auth_group_change", args=[group.pk])).status_code == 403
    client.post(
        reverse("admin:auth_group_change", args=[group.pk]),
        {"name": "Support", "permissions": [escalation.pk]},
    )
    client.post(reverse("admin:auth_group_add"), {"name": "Mine", "permissions": [escalation.pk]})
    client.post(reverse("admin:auth_group_delete", args=[group.pk]), {"post": "yes"})
    assert list(group.permissions.all()) == []
    assert not Group.objects.filter(name="Mine").exists()
    assert Group.objects.filter(pk=group.pk).exists()
    index = client.get(reverse("admin:index"))
    assert reverse("admin:auth_group_changelist") not in index.content.decode()


def test_a_superuser_still_edits_groups(client: Client, superuser: User) -> None:
    group = Group.objects.create(name="Support")
    login(client, superuser, fresh=True)
    response = client.post(
        reverse("admin:auth_group_change", args=[group.pk]),
        {"name": "Support", "permissions": [_permission("vendors.change_vendor").pk]},
    )
    assert response.status_code == 302, response.content.decode()
    assert [p.codename for p in group.permissions.all()] == ["change_vendor"]
