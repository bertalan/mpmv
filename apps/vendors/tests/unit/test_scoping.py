"""CORE.3: `vendor_scope` declarations are checked without the database."""

import pytest

from apps.accounts.models import User
from apps.vendors.models import Vendor, VendorMember, VendorTranslation
from apps.vendors.scoping import scope_path, scope_target


def test_scope_paths_lead_to_vendor() -> None:
    assert scope_path(Vendor) == "pk"
    assert scope_path(VendorMember) == "vendor"
    assert scope_target(VendorTranslation) is Vendor


def test_a_model_without_a_scope_path_is_refused() -> None:
    with pytest.raises(TypeError, match=r"accounts\.User must declare `vendor_scope`"):
        scope_path(User)


def test_a_scope_path_through_another_foreign_key_leads_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(VendorMember, "vendor_scope", "user")
    assert scope_target(VendorMember) is User


@pytest.mark.parametrize(
    ("path", "error"),
    [("role", "role is not a ForeignKey"), ("supplier", "has no field named 'supplier'")],
)
def test_a_scope_path_must_follow_foreign_keys(
    monkeypatch: pytest.MonkeyPatch, path: str, error: str
) -> None:
    monkeypatch.setattr(VendorMember, "vendor_scope", path)
    with pytest.raises(TypeError, match=error):
        scope_target(VendorMember)
