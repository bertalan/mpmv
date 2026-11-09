"""CORE.17: the navigation registry, where each section declares its space and permission."""

from types import SimpleNamespace
from typing import Any

import pytest

from apps.manager.navigation import Registry, Section, anyone, vendor_action
from apps.manager.spaces import Space


def _ctx(space: Space, *, role: str | None = None, vendor: object | None = None) -> Any:
    return SimpleNamespace(space=space, role=role, vendor=vendor, user=None)


def _section(key: str, space: Space = Space.SHOP, **kwargs: Any) -> Section:
    return Section(key=key, space=space, label=key, url_name=f"x:{key}", **kwargs)


def test_a_key_is_registered_once() -> None:
    registry = Registry()
    registry.register(_section("shop.dashboard"))
    with pytest.raises(ValueError, match=r"shop\.dashboard"):
        registry.register(_section("shop.dashboard"))


def test_get_answers_the_registered_section_or_none() -> None:
    registry = Registry()
    section = registry.register(_section("shop.orders"))
    assert registry.get("shop.orders") is section
    assert registry.get("shop.missing") is None


def test_the_menu_lists_only_the_sections_of_the_actor_space_that_it_may_open() -> None:
    registry = Registry()
    registry.register(_section("shop.dashboard", order=10))
    registry.register(_section("shop.payouts", order=50, allowed=lambda ctx: ctx.role == "owner"))
    registry.register(_section("shop.orders", order=20))
    registry.register(_section("shop.picker", order=5, in_menu=False))
    registry.register(_section("platform.dashboard", Space.PLATFORM, order=10))

    staff = _ctx(Space.SHOP, role="staff", vendor=object())
    owner = _ctx(Space.SHOP, role="owner", vendor=object())
    platform = _ctx(Space.PLATFORM)

    assert [s.key for s in registry.menu(staff)] == ["shop.dashboard", "shop.orders"]
    assert [s.key for s in registry.menu(owner)] == [
        "shop.dashboard",
        "shop.orders",
        "shop.payouts",
    ]
    assert [s.key for s in registry.menu(platform)] == ["platform.dashboard"]


def test_a_section_allows_only_its_own_space() -> None:
    section = _section("platform.dashboard", Space.PLATFORM)
    assert section.allows(_ctx(Space.PLATFORM))
    assert not section.allows(_ctx(Space.SHOP, role="owner", vendor=object()))


def test_a_shop_section_needs_a_selected_vendor_unless_it_says_otherwise() -> None:
    assert not _section("shop.orders").allows(_ctx(Space.SHOP, role="owner"))
    assert _section("shop.picker", needs_vendor=False).allows(_ctx(Space.SHOP))


def test_vendor_action_checks_the_member_role_against_the_vendor_matrix() -> None:
    payouts = vendor_action("payouts")
    orders = vendor_action("orders")
    assert payouts(_ctx(Space.SHOP, role="owner", vendor=object()))
    assert not payouts(_ctx(Space.SHOP, role="staff", vendor=object()))
    assert orders(_ctx(Space.SHOP, role="staff", vendor=object()))
    assert not orders(_ctx(Space.SHOP, role=None, vendor=object()))


def test_vendor_action_refuses_an_unknown_action_when_declared() -> None:
    with pytest.raises(ValueError, match="unknown vendor action"):
        vendor_action("launch_rockets")


def test_anyone_lets_every_actor_of_the_space_in() -> None:
    assert anyone(_ctx(Space.PLATFORM))
