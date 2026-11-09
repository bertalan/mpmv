"""CAT.6: staff manage redirects in the Django admin through the audited services.

The form refuses an off-site target, a loop and a chain; additions, edits and deletions are
audited (SEC.4).
"""

from collections.abc import Callable
from typing import Any

import pytest
from django.apps import apps
from django.contrib import admin
from django.db.models import QuerySet
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.admin import RedirectAdmin
from apps.catalog.models import Redirect
from apps.catalog.services import save_redirect
from apps.core.audit import AuditContext
from apps.core.checks import audit_policy, audit_policy_errors
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")
MfaLogin = Callable[[Client, User], Client]


def entries() -> "QuerySet[AuditLog]":
    return AuditLog.objects.filter(action__startswith="catalog.redirect.")


@pytest.fixture
def staff_client(client: Client, superuser: User, mfa_login: MfaLogin) -> Client:
    return mfa_login(client, superuser)


def form(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_path": "/old/",
        "match_prefix": "",
        "code": "301",
        "target_kind": "path",
        "target_path": "/new/",
        "target_product": "",
        "target_term": "",
        "target_query": "",
        "origin": "manual",
        "expires_at_0": "",
        "expires_at_1": "",
        "note": "",
    }
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def test_redirects_are_audited_without_an_exemption() -> None:
    policy = audit_policy([apps.get_app_config("catalog")])
    assert policy["catalog.redirect"] is None
    model_admin = admin.site._registry[Redirect]
    assert isinstance(model_admin, RedirectAdmin)
    assert audit_policy_errors([Redirect], {Redirect: model_admin}, policy) == []


def test_staff_add_a_redirect_and_it_is_audited(staff_client: Client) -> None:
    response = staff_client.post(reverse("admin:catalog_redirect_add"), form())
    assert response.status_code == 302, response.content
    assert Redirect.objects.get().target_path == "/new/"
    assert entries().get().action == "catalog.redirect.created"


def test_staff_edit_and_delete_a_redirect_with_audit(staff_client: Client) -> None:
    redirect = Redirect(source_path="/old/", target_kind="path", target_path="/new/")
    save_redirect(redirect, ctx=CTX)
    url = reverse("admin:catalog_redirect_change", args=[redirect.pk])
    response = staff_client.post(url, form(target_path="/newer/"))
    assert response.status_code == 302, response.content
    assert entries().filter(action="catalog.redirect.updated").count() == 1

    url = reverse("admin:catalog_redirect_delete", args=[redirect.pk])
    assert staff_client.post(url, {"post": "yes"}).status_code == 302
    assert not Redirect.objects.exists()
    assert entries().filter(action="catalog.redirect.deleted").count() == 1


def test_the_bulk_delete_action_is_audited(staff_client: Client) -> None:
    for source in ("/a/", "/b/"):
        save_redirect(
            Redirect(source_path=source, target_kind="path", target_path="/new/"), ctx=CTX
        )
    data = {
        "action": "delete_selected",
        "_selected_action": [str(pk) for pk in Redirect.objects.values_list("pk", flat=True)],
        "post": "yes",
    }
    assert staff_client.post(reverse("admin:catalog_redirect_changelist"), data).status_code == 302
    assert not Redirect.objects.exists()
    assert entries().filter(action="catalog.redirect.deleted").count() == 2


@pytest.mark.parametrize("target", ["https://example.com/", "//example.com/", "/\\example.com/"])
def test_the_form_refuses_an_off_site_target(staff_client: Client, target: str) -> None:
    response = staff_client.post(reverse("admin:catalog_redirect_add"), form(target_path=target))
    assert response.status_code == 200
    assert response.context["adminform"].form.errors["target_path"]
    assert not Redirect.objects.exists()


def test_the_form_refuses_a_chain(staff_client: Client) -> None:
    save_redirect(Redirect(source_path="/new/", target_kind="path", target_path="/x/"), ctx=CTX)
    response = staff_client.post(reverse("admin:catalog_redirect_add"), form())
    assert response.status_code == 200
    assert "/new/" in str(response.context["adminform"].form.errors)
    assert Redirect.objects.count() == 1


def test_the_list_filters_by_origin_and_searches_sources(staff_client: Client) -> None:
    save_redirect(Redirect(source_path="/old/", target_kind="path", target_path="/new/"), ctx=CTX)
    url = reverse("admin:catalog_redirect_changelist")
    response = staff_client.get(url, {"q": "old", "origin__exact": "manual"})
    assert response.status_code == 200
    assert b"/old/" in response.content
