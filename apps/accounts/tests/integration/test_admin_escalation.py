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


# --- Accounts that hold permissions without staff status are privileged too (review M1)
#
# A Wagtail editor gets `wagtailadmin.access_admin` from a group and needs no staff status
# (spec CMS.1). Taking over that account (password, then MFA) would give the attacker
# permissions they do not hold, so it is managed by superusers only, like a staff account.


def _group_editor() -> User:
    editor = UserFactory.create(email="editor@example.com")
    group = Group.objects.create(name="Test editors")
    group.permissions.add(_permission("wagtailadmin.access_admin"))
    editor.groups.add(group)
    return editor


def _privileged(role: str, superuser: User) -> User:
    """A target account that holds access: staff, superuser, a group or a direct permission."""
    if role == "superuser":
        return superuser
    if role == "staff":
        return UserFactory.create(email="colleague@example.com", staff=True)
    if role == "group":
        return _group_editor()
    assert role == "permission"
    return UserFactory.create(email="perm@example.com", permissions=["wagtailadmin.access_admin"])


NON_STAFF_ROLES = pytest.mark.parametrize("role", ["group", "permission"])
PRIVILEGED_ROLES = pytest.mark.parametrize("role", ["superuser", "staff", "group", "permission"])


@FRESHNESS
@NON_STAFF_ROLES
def test_staff_cannot_set_the_password_of_an_account_that_holds_permissions(
    client: Client, attacker: User, superuser: User, fresh: bool, role: str
) -> None:
    target = _privileged(role, superuser)
    login(client, attacker, fresh=fresh)
    old_hash = target.password
    status, password = _set_password(client, target)
    assert status in (302, 403)
    target.refresh_from_db()
    assert target.password == old_hash
    assert not target.check_password(password)


@NON_STAFF_ROLES
def test_a_fresh_staff_member_refused_with_403_on_a_permission_holder_password(
    client: Client, attacker: User, superuser: User, role: str
) -> None:
    target = _privileged(role, superuser)
    login(client, attacker, fresh=True)
    url = reverse("admin:auth_user_password_change", args=[target.pk])
    assert client.get(url).status_code == 403
    assert _set_password(client, target)[0] == 403


@FRESHNESS
@NON_STAFF_ROLES
def test_staff_cannot_reset_mfa_of_an_account_that_holds_permissions(
    client: Client, attacker: User, superuser: User, fresh: bool, role: str
) -> None:
    target = _privileged(role, superuser)
    TOTPDeviceFactory.create(user=target)
    customer = UserFactory.create(email="customer@example.com")
    TOTPDeviceFactory.create(user=customer)
    login(client, attacker, fresh=fresh)
    _reset_mfa(client, customer, target)
    assert services.has_second_factor(target)
    assert services.has_second_factor(customer)


def test_a_superuser_still_manages_a_group_editor(client: Client, superuser: User) -> None:
    editor = _group_editor()
    TOTPDeviceFactory.create(user=editor)
    login(client, superuser, fresh=True)
    status, password = _set_password(client, editor)
    assert status == 302
    editor.refresh_from_db()
    assert editor.check_password(password)
    _reset_mfa(client, editor)
    assert not services.has_second_factor(editor)


# --- Only a superuser deactivates, re-addresses or deletes a privileged account (review M2)


@pytest.fixture
def deleter() -> User:
    """The attacker's colleague who may also delete users."""
    return UserFactory.create(
        email="deleter@example.com",
        staff=True,
        permissions=["accounts.view_user", "accounts.change_user", "accounts.delete_user"],
    )


@FRESHNESS
@PRIVILEGED_ROLES
def test_staff_cannot_deactivate_or_re_address_a_privileged_account(
    client: Client, attacker: User, superuser: User, fresh: bool, role: str
) -> None:
    target = _privileged(role, superuser)
    email = target.email
    login(client, attacker, fresh=fresh)
    client.post(
        reverse("admin:accounts_user_change", args=[target.pk]),
        user_form(target, is_active="", email="taken-over@example.com"),
    )
    target.refresh_from_db()
    assert target.is_active
    assert target.email == email


@PRIVILEGED_ROLES
def test_email_and_active_flag_are_read_only_on_a_privileged_account(
    client: Client, attacker: User, superuser: User, role: str
) -> None:
    target = _privileged(role, superuser)
    login(client, attacker, fresh=True)
    response = client.get(reverse("admin:accounts_user_change", args=[target.pk]))
    assert response.status_code == 200
    form_fields = response.context["adminform"].form.fields
    assert "email" not in form_fields
    assert "is_active" not in form_fields


def test_staff_cannot_re_address_their_own_account(client: Client, attacker: User) -> None:
    login(client, attacker, fresh=True)
    client.post(
        reverse("admin:accounts_user_change", args=[attacker.pk]),
        user_form(attacker, email="elsewhere@example.com"),
    )
    attacker.refresh_from_db()
    assert attacker.email == "support@example.com"


def test_staff_can_still_deactivate_and_re_address_a_customer(
    client: Client, attacker: User
) -> None:
    customer = UserFactory.create(email="customer@example.com")
    login(client, attacker, fresh=True)
    response = client.post(
        reverse("admin:accounts_user_change", args=[customer.pk]),
        user_form(customer, is_active="", email="customer2@example.com"),
    )
    assert response.status_code == 302, response.content.decode()
    customer.refresh_from_db()
    assert not customer.is_active
    assert customer.email == "customer2@example.com"


def test_a_superuser_still_deactivates_and_re_addresses_a_staff_account(
    client: Client, superuser: User
) -> None:
    colleague = UserFactory.create(email="colleague@example.com", staff=True)
    login(client, superuser, fresh=True)
    response = client.post(
        reverse("admin:accounts_user_change", args=[colleague.pk]),
        user_form(colleague, is_active="", email="colleague2@example.com"),
    )
    assert response.status_code == 302, response.content.decode()
    colleague.refresh_from_db()
    assert not colleague.is_active
    assert colleague.email == "colleague2@example.com"


def _delete_one(client: Client, target: User) -> Any:
    return client.post(reverse("admin:accounts_user_delete", args=[target.pk]), {"post": "yes"})


def _delete_batch(client: Client, *targets: User) -> Any:
    return client.post(
        reverse("admin:accounts_user_changelist"),
        {
            "action": "delete_selected",
            "_selected_action": [t.pk for t in targets],
            "post": "yes",
        },
    )


def _exists(user: User) -> bool:
    return User.objects.filter(pk=user.pk).exists()


@FRESHNESS
@PRIVILEGED_ROLES
def test_staff_cannot_delete_a_privileged_account(
    client: Client, deleter: User, superuser: User, fresh: bool, role: str
) -> None:
    target = _privileged(role, superuser)
    login(client, deleter, fresh=fresh)
    url = reverse("admin:accounts_user_delete", args=[target.pk])
    get = client.get(url)
    post = _delete_one(client, target)
    assert _exists(target)
    if fresh:
        assert get.status_code == 403
        assert post.status_code == 403


@FRESHNESS
@PRIVILEGED_ROLES
def test_a_bulk_delete_with_a_privileged_account_is_refused_as_a_whole(
    client: Client, deleter: User, superuser: User, fresh: bool, role: str
) -> None:
    target = _privileged(role, superuser)
    customer = UserFactory.create(email="customer@example.com")
    login(client, deleter, fresh=fresh)
    _delete_batch(client, customer, target)
    assert _exists(target)
    assert _exists(customer)


def test_deleting_a_user_needs_a_fresh_step_up(client: Client, deleter: User) -> None:
    customer = UserFactory.create(email="customer@example.com")
    login(client, deleter)
    url = reverse("admin:accounts_user_delete", args=[customer.pk])
    for response in (client.get(url), _delete_one(client, customer)):
        assert response.status_code == 302
        assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    _delete_batch(client, customer)
    assert _exists(customer)


def test_a_superuser_also_needs_a_fresh_step_up_to_delete(client: Client, superuser: User) -> None:
    colleague = UserFactory.create(email="colleague@example.com", staff=True)
    login(client, superuser)
    response = _delete_one(client, colleague)
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    _delete_batch(client, colleague)
    assert _exists(colleague)


def test_staff_can_still_delete_customers_after_stepping_up(client: Client, deleter: User) -> None:
    one = UserFactory.create(email="one@example.com")
    two = UserFactory.create(email="two@example.com")
    three = UserFactory.create(email="three@example.com")
    login(client, deleter, fresh=True)
    assert _delete_one(client, one).status_code == 302
    assert _delete_batch(client, two, three).status_code == 302
    assert not any(_exists(user) for user in (one, two, three))


def test_a_superuser_still_deletes_privileged_accounts(client: Client, superuser: User) -> None:
    colleague = UserFactory.create(email="colleague@example.com", staff=True)
    other = UserFactory.create(email="other@example.com", superuser=True)
    editor = _group_editor()
    login(client, superuser, fresh=True)
    assert _delete_one(client, colleague).status_code == 302
    assert _delete_batch(client, other, editor).status_code == 302
    assert not any(_exists(user) for user in (colleague, other, editor))
