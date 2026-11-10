"""CORE.17: the Manager's two spaces, one URL prefix each (spec 02 §Backoffice)."""

import pytest

from apps.manager.spaces import MANAGER_PREFIX, PREFIXES, Space, space_of


def test_each_space_has_its_own_prefix_under_the_manager() -> None:
    assert PREFIXES == {Space.SHOP: "/manager/shop/", Space.PLATFORM: "/manager/platform/"}
    assert all(prefix.startswith(MANAGER_PREFIX) for prefix in PREFIXES.values())


@pytest.mark.parametrize(
    ("path", "space"),
    [
        ("/manager/shop/", Space.SHOP),
        ("/manager/shop/orders/12/", Space.SHOP),
        ("/manager/platform/", Space.PLATFORM),
        ("/manager/platform/staff/", Space.PLATFORM),
    ],
)
def test_a_path_under_a_gated_prefix_belongs_to_that_space(path: str, space: Space) -> None:
    assert space_of(path) is space


@pytest.mark.parametrize(
    "path",
    ["/manager/", "/manager/login/", "/manager/shopping/", "/manager/shop", "/shop/", "/"],
)
def test_any_other_path_belongs_to_no_space(path: str) -> None:
    assert space_of(path) is None
