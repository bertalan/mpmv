"""CORE.8 part 3/3: the editor roles are data (`apps.cms.roles`); the data stays consistent."""

import pytest

from apps.cms import roles

pytestmark = pytest.mark.unit


def test_role_names_are_unique_and_never_wagtails_defaults() -> None:
    assert len(set(roles.ROLE_NAMES)) == len(roles.ROLE_NAMES)
    assert not {roles.DEFAULT_EDITORS, roles.DEFAULT_MODERATORS} & set(roles.ROLE_NAMES)


def test_no_role_is_bound_to_the_home_page() -> None:
    """A grant on the home page would inherit to the whole tree and defeat the split."""
    assert not any(label.endswith("HomePage") for label in roles.ROLE_PAGE_MODELS)


def test_every_default_group_has_successor_roles_that_exist() -> None:
    for successors in roles.DEFAULT_GROUP_SUCCESSORS.values():
        assert successors
        assert set(successors) <= set(roles.ROLE_NAMES)


def test_the_menu_allow_list_names_no_commerce_item() -> None:
    banned = ("vendor", "product", "order", "rate", "payout", "shipping", "tax")
    assert not [name for name in roles.ADMIN_MENU_ALLOWED if any(b in name for b in banned)]


def test_only_the_blog_editors_add_pages() -> None:
    adders = {role.name for role in roles.ROLES if roles.ADD in role.page_permissions}
    assert adders == {roles.BLOG_EDITORS}
