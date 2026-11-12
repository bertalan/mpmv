"""CORE.17: the base list view of the Manager (filters, pagination, bulk actions with dry-run).

The rows come from `for_actor` narrowed to the selected vendor (CORE.3): a member of two vendors
sees only the chosen vendor's rows. A bulk action first shows the dry-run `ChangeSet`, then runs
on confirmation, behind an MFA step-up when it is sensitive. Selected ids outside the list
answer 404 and run nothing.
"""

import time
from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.manager.navigation import registry
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

from . import views
from .conftest import PICKER

pytestmark = pytest.mark.django_db

LIST = "/manager/shop/test-members/"
UNSCOPED = "/manager/shop/test-unscoped-members/"


@pytest.fixture(autouse=True)
def _test_sections(settings: object) -> Iterator[None]:
    settings.ROOT_URLCONF = "apps.manager.tests.integration.urls"  # type: ignore[attr-defined]
    views.CALLS.clear()
    with registry.extended(*views.TEST_SECTIONS):
        yield
    views.CALLS.clear()


@pytest.fixture
def members(vendor: Vendor, owner: User) -> list[VendorMember]:
    """The owner plus three staff members of `vendor`, sorted by email."""
    for n in range(3):
        VendorMemberFactory.create(
            vendor=vendor, role="staff", user__email=f"staff{n}@shop-one.example.com"
        )
    return list(VendorMember.objects.filter(vendor=vendor).order_by("user__email"))


@pytest.fixture
def other_member(owner: User) -> VendorMember:
    """A member of another vendor the owner also belongs to."""
    other = VendorFactory.create(display_name="Shop Three")
    VendorMemberFactory.create(vendor=other, user=owner, role="owner")
    return VendorMemberFactory.create(
        vendor=other, role="staff", user__email="hidden@shop-three.example.com"
    )


def _ids(*objects: VendorMember) -> list[str]:
    return [str(obj.public_id) for obj in objects]


def test_the_list_shows_only_the_selected_vendor_rows(
    client: Client,
    owner: User,
    vendor: Vendor,
    members: list[VendorMember],
    other_member: VendorMember,
) -> None:
    verified_login(client, owner)
    client.post(PICKER, {"vendor": str(vendor.public_id)})
    page = client.get(LIST).text
    assert "hidden@shop-three.example.com" not in page
    assert members[0].user.email in page

    client.post(PICKER, {"vendor": str(other_member.vendor.public_id)})
    page = client.get(LIST).text
    assert "hidden@shop-three.example.com" in page
    assert members[1].user.email not in page


def test_the_list_paginates(client: Client, owner: User, members: list[VendorMember]) -> None:
    verified_login(client, owner)
    first = client.get(LIST).text
    second = client.get(LIST, {"page": "2"}).text
    assert members[0].user.email in first
    assert members[2].user.email not in first
    assert members[2].user.email in second
    assert members[0].user.email not in second
    assert "Page 1 of 2" in first
    assert client.get(LIST, {"page": "99"}).status_code == 200  # out of range: the last page


def test_a_filter_narrows_the_rows_and_an_unknown_value_is_ignored(
    client: Client, owner: User, members: list[VendorMember]
) -> None:
    verified_login(client, owner)
    owners = client.get(LIST, {"role": "owner"}).text
    assert owner.email in owners
    assert "staff0@shop-one.example.com" not in owners
    assert "staff0@shop-one.example.com" in client.get(LIST, {"role": "nobody"}).text


def test_a_bulk_action_first_shows_the_dry_run(
    client: Client, owner: User, members: list[VendorMember]
) -> None:
    verified_login(client, owner)
    chosen = members[1:3]
    response = client.post(LIST, {"action": "touch", "selected": _ids(*chosen)})
    assert response.status_code == 200
    assert [("touch", _ids(*chosen), True)] == views.CALLS
    for member in chosen:
        assert str(member.public_id) in response.text
    assert 'name="confirm"' in response.text


def test_a_confirmed_bulk_action_runs_and_returns_to_the_list(
    client: Client, owner: User, members: list[VendorMember]
) -> None:
    verified_login(client, owner)
    response = client.post(
        f"{LIST}?role=staff", {"action": "touch", "selected": _ids(members[1]), "confirm": "1"}
    )
    assert response.status_code == 302
    assert response["Location"] == f"{LIST}?role=staff"
    assert [("touch", _ids(members[1]), False)] == views.CALLS


def test_a_sensitive_bulk_action_needs_a_recent_verification(
    client: Client, owner: User, members: list[VendorMember]
) -> None:
    verified_login(client, owner, verified_at=time.time() - 3600)
    data = {"action": "sensitive", "selected": _ids(members[1]), "confirm": "1"}
    response = client.post(LIST, data)
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa:verify")
    assert all(not call[2] for call in views.CALLS)

    verified_login(client, owner)
    assert client.post(LIST, data).status_code == 302
    assert views.CALLS[-1] == ("sensitive", _ids(members[1]), False)


@pytest.mark.parametrize("confirm", [False, True])
def test_a_selected_row_of_another_vendor_is_404_and_nothing_runs(
    client: Client,
    owner: User,
    members: list[VendorMember],
    other_member: VendorMember,
    vendor: Vendor,
    confirm: bool,
) -> None:
    verified_login(client, owner)
    client.post(PICKER, {"vendor": str(vendor.public_id)})
    data = {"action": "touch", "selected": _ids(members[1], other_member)}
    if confirm:
        data["confirm"] = "1"
    assert client.post(LIST, data).status_code == 404
    assert views.CALLS == []


@pytest.mark.parametrize("selected", [["not-a-uuid"], ["00000000-0000-0000-0000-000000000000"]])
def test_a_malformed_or_unknown_id_is_404(
    client: Client, owner: User, members: list[VendorMember], selected: list[str]
) -> None:
    verified_login(client, owner)
    assert client.post(LIST, {"action": "touch", "selected": selected}).status_code == 404
    assert views.CALLS == []


def test_an_unknown_action_is_404(client: Client, owner: User, members: list[VendorMember]) -> None:
    verified_login(client, owner)
    response = client.post(LIST, {"action": "explode", "selected": _ids(members[1])})
    assert response.status_code == 404


def test_an_action_the_actor_may_not_run_is_404_and_hidden(
    client: Client, vendor: Vendor, members: list[VendorMember]
) -> None:
    staff = members[1].user
    verified_login(client, staff)
    assert 'value="owners_only"' not in client.get(LIST).text
    response = client.post(LIST, {"action": "owners_only", "selected": _ids(members[1])})
    assert response.status_code == 404
    assert views.CALLS == []


def test_an_empty_selection_asks_for_rows_and_runs_nothing(
    client: Client, owner: User, members: list[VendorMember]
) -> None:
    verified_login(client, owner)
    response = client.post(LIST, {"action": "touch"})
    assert response.status_code == 200
    assert "Select at least one row." in response.text
    assert views.CALLS == []


# --- Vendor scoping is enforced by the base class, not left to each list


def test_a_list_that_forgets_scoping_still_shows_only_the_selected_vendor_rows(
    client: Client,
    owner: User,
    vendor: Vendor,
    members: list[VendorMember],
    other_member: VendorMember,
) -> None:
    stranger = VendorMemberFactory.create(user__email="stranger@shop-four.example.com")
    verified_login(client, owner)
    client.post(PICKER, {"vendor": str(vendor.public_id)})
    page = client.get(UNSCOPED, {"page": "1"}).text + client.get(UNSCOPED, {"page": "2"}).text
    assert members[0].user.email in page
    assert "hidden@shop-three.example.com" not in page
    assert stranger.user.email not in page


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("foreign", ["member_vendor", "stranger_vendor"])
def test_a_list_that_forgets_scoping_refuses_ids_of_another_vendor(
    client: Client,
    owner: User,
    vendor: Vendor,
    members: list[VendorMember],
    other_member: VendorMember,
    foreign: str,
    confirm: bool,
) -> None:
    """Ids from another vendor (one the user also belongs to, or a stranger's) answer 404."""
    stranger = VendorMemberFactory.create()
    theirs = other_member if foreign == "member_vendor" else stranger
    verified_login(client, owner)
    client.post(PICKER, {"vendor": str(vendor.public_id)})
    for selected in (_ids(theirs), _ids(members[1], theirs)):
        data = {"action": "touch", "selected": selected}
        if confirm:
            data["confirm"] = "1"
        assert client.post(UNSCOPED, data).status_code == 404
    assert views.CALLS == []


def test_a_bulk_action_is_a_group_2_action_so_a_restricted_session_is_refused(
    client: Client, owner: User, members: list[VendorMember]
) -> None:
    data = {"action": "touch", "selected": _ids(members[1]), "confirm": "1"}
    verified_login(client, owner, level=levels.RESTRICTED)
    assert client.post(LIST, data).status_code == 403
    assert views.CALLS == []
    verified_login(client, owner, level=levels.OPERATIONAL)
    assert client.post(LIST, data).status_code == 302
    assert [("touch", _ids(members[1]), False)] == views.CALLS
