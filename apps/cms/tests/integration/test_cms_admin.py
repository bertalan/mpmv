"""CMS.1: the Wagtail admin is the editors' backoffice at /admin/, behind MFA (CORE.4).

Wagtail lets in whoever holds `wagtailadmin.access_admin`, staff or not, so the MFA policy
covers that permission too. Users and groups stay in the audited staff admin (SEC.4).
"""

import re
import secrets

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import User
from apps.accounts.services import mfa_required
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.models import NavigationMenu
from apps.cms.services import SeedReport, can_access_cms_admin

pytestmark = pytest.mark.django_db


def _editor(password: str | None = None) -> User:
    """A non-staff member of Wagtail's default "Editors" group (created by its migrations)."""
    user: User = UserFactory.create(password=password or secrets.token_urlsafe(16))
    user.groups.add(Group.objects.get(name="Editors"))
    return user


def test_the_admin_is_mounted_apart_from_the_staff_admin_and_the_api() -> None:
    assert reverse("wagtailadmin_home") == "/admin/"
    assert reverse("admin:index") == "/django-admin/"


def test_anonymous_visitors_are_sent_to_the_login(client: Client) -> None:
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login/")


def test_a_verified_staff_member_edits_pages(client: Client, seeded: SeedReport) -> None:
    verified_login(client, UserFactory.create(superuser=True))
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/pages/").status_code == 200
    assert client.get("/admin/snippets/cms/navigationmenu/").status_code == 200


def test_wagtail_editors_must_use_mfa_even_without_staff_status() -> None:
    editor = _editor()
    assert not editor.is_staff
    assert can_access_cms_admin(editor)
    assert mfa_required(editor)
    assert not can_access_cms_admin(UserFactory.create())


def test_a_password_only_editor_is_sent_to_mfa_enrolment(client: Client) -> None:
    password = secrets.token_urlsafe(16)
    editor = _editor(password)
    response = client.post("/admin/login/", {"username": editor.email, "password": password})
    assert response.status_code == 302
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("mfa:setup"))


def test_a_verified_editor_reaches_the_admin(client: Client) -> None:
    verified_login(client, _editor())
    assert client.get("/admin/").status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/admin/users/", "/admin/users/new/", "/admin/groups/", "/admin/bulk/accounts/user/delete/"],
)
def test_users_and_groups_are_not_managed_in_the_wagtail_admin(client: Client, path: str) -> None:
    verified_login(client, UserFactory.create(superuser=True))
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("name", ["wagtailusers_users:index", "wagtailusers_groups:index"])
def test_wagtail_user_and_group_views_are_not_registered(name: str) -> None:
    with pytest.raises(NoReverseMatch):
        reverse(name)


def test_the_admin_menu_has_no_users_or_groups(client: Client, seeded: SeedReport) -> None:
    verified_login(client, UserFactory.create(superuser=True))
    html = client.get("/admin/").content.decode()
    assert "/admin/users/" not in html
    assert "/admin/groups/" not in html


def test_admin_date_pickers_run_with_the_csp_nonce(client: Client, seeded: SeedReport) -> None:
    verified_login(client, UserFactory.create(superuser=True))
    response = client.get("/admin/pages/")  # the explorer's date filter has two pickers
    nonce = re.search(r"'nonce-([^']+)'", response["Content-Security-Policy"])
    assert nonce is not None
    html = response.content.decode()
    assert html.count(f'<script nonce="{nonce[1]}">initDateChooser(') == 2
    assert not re.search(r"<script>(?!</script>)", html)  # no inline script left without it


def test_admin_editors_edit_menus_without_inline_scripts(
    client: Client, seeded: SeedReport
) -> None:
    verified_login(client, UserFactory.create(superuser=True))
    menu = NavigationMenu.objects.get(locale__language_code="it", location="header")
    html = client.get(f"/admin/snippets/cms/navigationmenu/edit/{menu.pk}/").content.decode()
    assert "Chi siamo" in html
    assert not re.search(r"<script(?![^>]*\b(?:src|type)=)[^>]*>", html)


@pytest.mark.parametrize("path", ["/admin/account/change_password/", "/admin/password_reset/"])
def test_passwords_are_not_changed_from_the_wagtail_admin(client: Client, path: str) -> None:
    verified_login(client, UserFactory.create(superuser=True))
    assert client.get(path).status_code in {302, 404}
