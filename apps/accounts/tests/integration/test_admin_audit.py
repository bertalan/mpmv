"""SEC.4: staff changes to users, credentials and permissions are audited with before/after.

Covers the user admin (profile, staff and superuser flags, groups, permissions, password,
deletion) and the group admin, plus step-up on the staff audit view.
"""

import secrets
import time
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client: Client, superuser: User) -> Client:
    verified_login(client, superuser)
    return client


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


def test_granting_staff_access_is_audited_with_before_and_after(staff_client: Client) -> None:
    user = UserFactory.create(email="customer@example.com")
    response = staff_client.post(
        reverse("admin:accounts_user_change", args=[user.pk]), user_form(user, is_staff="on")
    )
    assert response.status_code == 302, response.content.decode()
    entry = AuditLog.objects.get(action="accounts.user.updated")
    assert entry.target_key == str(user.public_id)
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["is_staff"], entry.after["is_staff"]) == (False, True)
    assert entry.actor_label == "staff:staff@example.com"
    assert entry.source == "admin"


def test_permission_and_group_changes_are_audited(staff_client: Client) -> None:
    user = UserFactory.create(email="editor@example.com", staff=True)
    group = Group.objects.create(name="Support")
    permission = _permission("vendors.change_vendor")
    staff_client.post(
        reverse("admin:accounts_user_change", args=[user.pk]),
        user_form(user, groups=[group.pk], user_permissions=[permission.pk]),
    )
    entry = AuditLog.objects.get(action="accounts.user.access_changed")
    assert entry.target_key == str(user.public_id)
    assert entry.before == {"groups": [], "user_permissions": []}
    assert entry.after == {"groups": ["Support"], "user_permissions": ["vendors.change_vendor"]}


def test_saving_a_user_without_changes_writes_no_audit_entry(staff_client: Client) -> None:
    user = UserFactory.create(email="customer@example.com")
    staff_client.post(reverse("admin:accounts_user_change", args=[user.pk]), user_form(user))
    assert not AuditLog.objects.filter(target_key=str(user.public_id)).exists()


def test_creating_a_user_is_audited_without_the_password(staff_client: Client) -> None:
    password = secrets.token_urlsafe(16)
    response = staff_client.post(
        reverse("admin:accounts_user_add"),
        {
            "email": "new@example.com",
            "usable_password": "true",
            "password1": password,
            "password2": password,
        },
    )
    assert response.status_code == 302, response.content.decode()
    user = User.objects.get(email="new@example.com")
    entry = AuditLog.objects.get(action="accounts.user.created")
    assert entry.target_key == str(user.public_id)
    assert entry.after is not None
    assert entry.after["email"] == "new@example.com"
    assert entry.after["has_usable_password"] is True
    assert password not in str(entry.after)
    assert user.password not in str(entry.after)


def test_a_password_change_in_the_admin_is_audited_without_the_hash(staff_client: Client) -> None:
    user = UserFactory.create(email="customer@example.com")
    old_hash = user.password
    password = secrets.token_urlsafe(16)
    response = staff_client.post(
        reverse("admin:auth_user_password_change", args=[user.pk]),
        {"usable_password": "true", "password1": password, "password2": password},
    )
    assert response.status_code == 302, response.content.decode()
    user.refresh_from_db()
    entry = AuditLog.objects.get(action="accounts.user.password_changed")
    assert entry.target_key == str(user.public_id)
    assert entry.before == {"has_usable_password": True}
    assert entry.after == {"has_usable_password": True}
    stored = str(entry.before) + str(entry.after)
    assert old_hash not in stored
    assert user.password not in stored
    assert password not in stored


def test_changing_ones_own_password_in_the_admin_is_audited_without_the_hash(
    client: Client,
) -> None:
    old_password = secrets.token_urlsafe(16)
    staff = UserFactory.create(email="staff@example.com", superuser=True, password=old_password)
    verified_login(client, staff)
    old_hash = staff.password
    new_password = secrets.token_urlsafe(16)
    response = client.post(
        reverse("admin:password_change"),
        {
            "old_password": old_password,
            "new_password1": new_password,
            "new_password2": new_password,
        },
    )
    assert response.status_code == 302, response.content.decode()
    staff.refresh_from_db()
    assert staff.check_password(new_password)
    entry = AuditLog.objects.get(action="accounts.user.password_changed")
    assert entry.target_key == str(staff.public_id)
    assert entry.actor_label == "staff:staff@example.com"
    assert entry.source == "admin"
    assert entry.before == {"has_usable_password": True}
    assert entry.after == {"has_usable_password": True}
    stored = str(entry.before) + str(entry.after)
    for secret in (old_hash, staff.password, old_password, new_password):
        assert secret not in stored


def test_a_rejected_own_password_change_writes_no_audit_entry(client: Client) -> None:
    staff = UserFactory.create(email="staff@example.com", superuser=True)
    verified_login(client, staff)
    new_password = secrets.token_urlsafe(16)
    response = client.post(
        reverse("admin:password_change"),
        {
            "old_password": secrets.token_urlsafe(16),
            "new_password1": new_password,
            "new_password2": new_password,
        },
    )
    assert response.status_code == 200
    assert not AuditLog.objects.filter(action="accounts.user.password_changed").exists()


def test_deleting_a_user_is_audited(staff_client: Client) -> None:
    user = UserFactory.create(email="leaver@example.com")
    staff_client.post(reverse("admin:accounts_user_delete", args=[user.pk]), {"post": "yes"})
    assert not User.objects.filter(pk=user.pk).exists()
    entry = AuditLog.objects.get(action="accounts.user.deleted")
    assert entry.target_key == str(user.public_id)
    assert entry.before is not None
    assert entry.before["email"] == "leaver@example.com"
    assert entry.after is None


def test_group_permission_changes_are_audited(staff_client: Client) -> None:
    permission = _permission("vendors.change_vendor")
    staff_client.post(
        reverse("admin:auth_group_add"), {"name": "Support", "permissions": [permission.pk]}
    )
    assert Group.objects.filter(name="Support").exists()
    created = AuditLog.objects.get(action="auth.group.created")
    assert created.target_key == "Support"  # groups have no public id; their name is unique
    assert created.after == {"name": "Support"}
    access = AuditLog.objects.get(action="auth.group.access_changed")
    assert access.target_key == "Support"
    assert access.before == {"permissions": []}
    assert access.after == {"permissions": ["vendors.change_vendor"]}


def test_deleting_a_group_is_audited(staff_client: Client) -> None:
    group = Group.objects.create(name="Support")
    staff_client.post(reverse("admin:auth_group_delete", args=[group.pk]), {"post": "yes"})
    entry = AuditLog.objects.get(action="auth.group.deleted")
    assert entry.before == {"name": "Support", "permissions": []}


# --- Step-up on the staff audit view (CORE.4 `step_up_required`)


def test_the_audit_view_opens_after_a_fresh_verification(staff_client: Client) -> None:
    response = staff_client.get(reverse("admin:core_auditlog_changelist"))
    assert response.status_code == 200


def test_the_audit_view_asks_for_the_second_factor_again_after_a_while(
    client: Client, superuser: User
) -> None:
    verified_login(client, superuser, verified_at=time.time() - 3600)
    for url in (
        reverse("admin:core_auditlog_changelist"),
        reverse(
            "admin:core_auditlog_change",
            args=[AuditLog.objects.get(action="accounts.login.succeeded").pk],
        ),
    ):
        response = client.get(url)
        assert response.status_code == 302
        assert urlsplit(response["Location"]).path == reverse("mfa:verify")
