"""CORE.18 2/2: the notices section is gated by the role matrix (Catalog E, Owner R), with the
`compliance.review_notices` permission held by the Catalog group (spec 02 matrix)."""

import pytest
from django.contrib.auth.models import Group
from django.test import Client

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.compliance.models import Notice
from apps.compliance.tests.factories import NoticeFactory
from apps.manager import roles

pytestmark = pytest.mark.django_db
URL = "/manager/platform/notices/"


def test_the_catalog_group_holds_the_notice_permission() -> None:
    group = Group.objects.get(name=roles.role("catalog").group)
    labels = {
        f"{a}.{c}" for a, c in group.permissions.values_list("content_type__app_label", "codename")
    }
    assert "compliance.review_notices" in labels


def test_the_owner_group_holds_the_notice_permission() -> None:
    group = Group.objects.get(name=roles.role("owner").group)
    labels = {
        f"{a}.{c}" for a, c in group.permissions.values_list("content_type__app_label", "codename")
    }
    assert "compliance.review_notices" in labels


def test_a_role_only_owner_can_read_notices(client: Client) -> None:
    verified_login(client, UserFactory.create(platform_role="owner"))
    assert client.get(URL).status_code == 200


def test_migration_0003_gives_existing_catalog_and_owner_groups_the_permission() -> None:
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module("apps.manager.migrations.0003_catalog_review_notices")
    for key in ("catalog", "owner"):
        group = Group.objects.get(name=roles.role(key).group)
        group.permissions.remove(*group.permissions.filter(codename="review_notices"))
    migration.seed(django_apps, None)
    for key in ("catalog", "owner"):
        group = Group.objects.get(name=roles.role(key).group)
        assert group.permissions.filter(codename="review_notices").exists()


def test_a_catalog_user_reviews_notices(client: Client) -> None:
    notice = NoticeFactory.create()
    verified_login(client, UserFactory.create(platform_role="catalog"))
    assert client.get(URL).status_code == 200
    assert client.get(f"{URL}{notice.public_id}/").status_code == 200
    response = client.post(f"{URL}{notice.public_id}/", {"action": "review"})
    assert response.status_code == 302
    notice.refresh_from_db()
    assert notice.status == Notice.Status.UNDER_REVIEW


@pytest.mark.parametrize("key", ["config", "finance", "support"])
def test_the_other_roles_get_404_on_notices(client: Client, key: str) -> None:
    notice = NoticeFactory.create()
    verified_login(client, UserFactory.create(platform_role=key))
    assert client.get(URL).status_code == 404
    assert client.post(f"{URL}{notice.public_id}/", {"action": "review"}).status_code == 404
    notice.refresh_from_db()
    assert notice.status != Notice.Status.UNDER_REVIEW


def test_the_gate_reads_the_matrix_not_the_bare_permission(client: Client) -> None:
    """The permission alone, held by a role the matrix keeps out (Support), opens nothing."""
    user = UserFactory.create(platform_role="support", permissions=["compliance.review_notices"])
    verified_login(client, user)
    assert client.get(URL).status_code == 404
