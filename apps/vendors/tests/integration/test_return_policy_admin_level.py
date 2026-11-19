"""VEN.40 3/3: approving or rejecting a return policy in the admin is a sensitive action
(`return_policy_review`, CORE.25): a passkey window or a fresh passkey is needed."""

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse
from django.utils.module_loading import import_string

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.factories import PasskeyDeviceFactory, UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.vendors.models import ReturnPolicyVersion
from apps.vendors.tests.factories import ReturnPolicyVersionFactory

pytestmark = pytest.mark.django_db

LIST = "admin:vendors_returnpolicyversion_changelist"


def role_user(key: str) -> User:
    group = import_string("apps.manager.roles.role")(key).group
    user = UserFactory.create(is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


def client_for(key: str, *, level: str) -> Client:
    user = role_user(key)
    PasskeyDeviceFactory.create(user=user)
    client = Client()
    verified_login(client, user, level=level)
    return client


def review(client: Client, version: ReturnPolicyVersion, action: str) -> int:
    response = client.post(
        reverse(LIST),
        {"action": action, "_selected_action": [version.pk]},
    )
    return response.status_code


@pytest.mark.parametrize("action", ["approve_selected", "reject_selected"])
def test_a_review_without_a_passkey_window_is_refused_and_changes_nothing(action: str) -> None:
    version = ReturnPolicyVersionFactory.create(status=ReturnPolicyVersion.Status.PENDING)
    client = client_for("catalog", level=levels.NO_WINDOW)
    assert review(client, version, action) == 403
    version.refresh_from_db()
    assert version.status == ReturnPolicyVersion.Status.PENDING


@pytest.mark.parametrize(
    ("action", "status"),
    [
        ("approve_selected", ReturnPolicyVersion.Status.APPROVED),
        ("reject_selected", ReturnPolicyVersion.Status.REJECTED),
    ],
)
def test_a_review_with_a_passkey_window_succeeds(action: str, status: str) -> None:
    version = ReturnPolicyVersionFactory.create(status=ReturnPolicyVersion.Status.PENDING)
    client = client_for("catalog", level=levels.PASSKEY_WINDOW)
    review(client, version, action)
    version.refresh_from_db()
    assert version.status == status


def test_a_role_without_the_cell_is_refused_even_with_a_window() -> None:
    version = ReturnPolicyVersionFactory.create(status=ReturnPolicyVersion.Status.PENDING)
    client = client_for("finance", level=levels.PASSKEY_WINDOW)
    review(client, version, "approve_selected")
    version.refresh_from_db()
    assert version.status == ReturnPolicyVersion.Status.PENDING
