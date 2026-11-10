"""CORE.8 part 3/3 (owner decision Q-58): own editor roles per part of the page tree.

A blog editor cannot edit the About page or a legal page; Wagtail's default Editors and
Moderators groups no longer exist; the Wagtail admin menu has no vendor, product, order, rate
or payout item, for platform staff and editors alike.
"""

import re
from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.test import Client, RequestFactory
from django.urls import reverse
from wagtail.admin.menu import admin_menu
from wagtail.models import GroupCollectionPermission, GroupPagePermission, Page

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms import roles
from apps.cms.editor_roles import grant_role_on_page, sync_editor_roles
from apps.cms.models import BlogIndexPage
from apps.cms.tests.factories import (
    BlogPostPageFactory,
    LegalPageFactory,
    StandardPageFactory,
    editor_user,
)

pytestmark = pytest.mark.django_db


def _fresh(user: Any) -> User:
    """Reload the user: Django caches permissions on the instance."""
    return User.objects.get(pk=user.pk)


def _can_edit(user: Any, page: Page) -> bool:
    return bool(page.permissions_for_user(_fresh(user)).can_edit())


@pytest.fixture
def tree() -> dict[str, Page]:
    about = StandardPageFactory.create(title="About us")
    legal = LegalPageFactory.create()
    post = BlogPostPageFactory.create()
    blog = BlogIndexPage.objects.get(pk=post.get_parent().pk)
    return {"about": about, "legal": legal, "post": post, "blog": blog}


def test_the_default_groups_are_gone_and_the_roles_exist() -> None:
    assert not Group.objects.filter(
        name__in=[roles.DEFAULT_EDITORS, roles.DEFAULT_MODERATORS]
    ).exists()
    assert set(Group.objects.filter(name__in=roles.ROLE_NAMES).values_list("name", flat=True)) == (
        set(roles.ROLE_NAMES)
    )


@pytest.mark.parametrize(
    ("role", "editable", "refused"),
    [
        (roles.BLOG_EDITORS, ["post", "blog"], ["about", "legal"]),
        (roles.LEGAL_EDITORS, ["legal"], ["about", "post", "blog"]),
        (roles.PAGE_EDITORS, ["about"], ["legal", "post", "blog"]),
        (roles.EVENT_MODERATORS, [], ["about", "legal", "post", "blog"]),
    ],
)
def test_each_role_edits_only_its_part_of_the_tree(
    tree: dict[str, Page], role: str, editable: list[str], refused: list[str]
) -> None:
    user = editor_user(role)
    assert {key: _can_edit(user, tree[key]) for key in editable} == dict.fromkeys(editable, True)
    assert {key: _can_edit(user, tree[key]) for key in refused} == dict.fromkeys(refused, False)


def test_a_blog_editor_cannot_open_the_about_or_a_legal_page_editor(
    client: Client, tree: dict[str, Page]
) -> None:
    verified_login(client, editor_user(roles.BLOG_EDITORS))
    assert (
        client.get(reverse("wagtailadmin_pages:edit", args=(tree["post"].pk,))).status_code == 200
    )
    for key in ("about", "legal"):
        response = client.get(reverse("wagtailadmin_pages:edit", args=(tree[key].pk,)))
        assert response.status_code == 302, key  # Wagtail sends a denied editor home
        assert response["Location"] == reverse("wagtailadmin_home"), key


def test_a_blog_editor_cannot_change_the_about_page_by_posting(
    client: Client, tree: dict[str, Page]
) -> None:
    verified_login(client, editor_user(roles.BLOG_EDITORS))
    about = tree["about"]
    client.post(
        reverse("wagtailadmin_pages:edit", args=(about.pk,)),
        {"title": "Hijacked", "slug": about.slug, "action-publish": "Publish"},
    )
    about.refresh_from_db()
    assert about.title != "Hijacked"
    assert (
        about.get_latest_revision() is None
        or about.get_latest_revision().content.get("title") != "Hijacked"
    )


def test_only_blog_editors_add_posts(tree: dict[str, Page]) -> None:
    blog_editor = _fresh(editor_user(roles.BLOG_EDITORS))
    legal_editor = _fresh(editor_user(roles.LEGAL_EDITORS))
    assert tree["blog"].permissions_for_user(blog_editor).can_add_subpage()
    assert not tree["blog"].permissions_for_user(legal_editor).can_add_subpage()
    assert not tree["about"].permissions_for_user(blog_editor).can_add_subpage()


def test_a_page_created_later_is_covered_by_its_role(tree: dict[str, Page]) -> None:
    second_legal = LegalPageFactory.create()
    user = editor_user(roles.LEGAL_EDITORS)
    assert _can_edit(user, second_legal)
    assert _can_edit(user, tree["legal"])


def test_the_sync_is_idempotent_and_never_removes_grants(tree: dict[str, Page]) -> None:
    group = Group.objects.get(name=roles.LEGAL_EDITORS)
    before = GroupPagePermission.objects.filter(group=group).count()
    assert before > 0
    sync_editor_roles()
    sync_editor_roles()
    grant_role_on_page(roles.LEGAL_EDITORS, tree["legal"])
    assert GroupPagePermission.objects.filter(group=group).count() == before


def test_roles_open_the_admin_and_the_snippets_they_need() -> None:
    blog_editor = _fresh(editor_user(roles.BLOG_EDITORS))
    assert blog_editor.has_perm("wagtailadmin.access_admin")
    granted = set(
        GroupCollectionPermission.objects.filter(group__user=blog_editor).values_list(
            "permission__codename", flat=True
        )
    )
    assert {"add_image", "change_image", "choose_image"} <= granted
    assert blog_editor.has_perm("cms.add_blogauthor")
    legal_editor = _fresh(editor_user(roles.LEGAL_EDITORS))
    assert legal_editor.has_perm("wagtailadmin.access_admin")
    assert not legal_editor.has_perm("cms.add_blogauthor")


# --- The Wagtail admin menu: CMS only


def _menu(user: Any) -> list[tuple[str, str]]:
    request = RequestFactory().get("/admin/")
    request.user = _fresh(user)
    found: list[tuple[str, str]] = []
    for item in admin_menu.menu_items_for_request(request):
        found.append((item.name, str(item.label)))
        submenu = getattr(item, "menu", None)
        if submenu is not None:
            for sub in submenu.menu_items_for_request(request):
                found.append((f"{item.name}/{sub.name}", str(sub.label)))
    return found


COMMERCE = re.compile(r"vendor|product|order|rate|payout|shipping|tax|ledger", re.IGNORECASE)


@pytest.mark.parametrize("who", ["superuser", "staff_moderator", *roles.ROLE_NAMES])
def test_the_admin_menu_has_no_commerce_item(who: str) -> None:
    if who == "superuser":
        user = UserFactory.create(superuser=True)
    elif who == "staff_moderator":
        user = UserFactory.create(staff=True)
        user.groups.add(Group.objects.get(name=roles.EVENT_MODERATORS))
    else:
        user = editor_user(who)
    entries = _menu(user)
    assert entries
    top = {name for name, _ in entries if "/" not in name}
    assert top <= roles.ADMIN_MENU_ALLOWED, top - roles.ADMIN_MENU_ALLOWED
    for name, label in entries:
        assert not COMMERCE.search(name), name
        assert not COMMERCE.search(label), label


def test_the_vendor_policies_entry_is_not_in_the_events_menu() -> None:
    names = {name for name, _ in _menu(UserFactory.create(superuser=True))}
    assert "events/vendor-policies" not in names
    assert "events/awaiting-review" in names
