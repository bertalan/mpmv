"""MIG.21 (MIG.18 in lane-a's wording): legacy store reviews and enquiries exported to each vendor.

Done when: each vendor downloads a file with only its own rows from the Manager shop space (owner
only), the download is audited without personal data, staff may generate it on a vendor's behalf
(audited), and another vendor's rows never appear (query parameter tampering gives 404 and an
audit entry). Nothing is created in the new system.
"""

import csv
import io
from collections.abc import Iterator

import pytest
from django.test import Client
from django.test.utils import override_settings

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.manager import roles
from apps.manager.vendor_choice import SESSION_KEY
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

from .legacy_rows import LEGACY_A, LEGACY_B, SOURCE, rows

pytestmark = pytest.mark.django_db

SHOP_URL = "/manager/shop/legacy-export/"
DOWNLOADED = "vendor.legacy_export.downloaded"
REFUSED = "vendor.legacy_export.refused"


@pytest.fixture(autouse=True)
def source() -> Iterator[None]:
    with override_settings(LEGACY_EXPORT_SOURCE=SOURCE):
        yield


@pytest.fixture
def vendor_a() -> Vendor:
    return VendorFactory.create(display_name="Shop A", legacy_wp_user_id=LEGACY_A)


@pytest.fixture
def vendor_b() -> Vendor:
    return VendorFactory.create(display_name="Shop B", legacy_wp_user_id=LEGACY_B)


@pytest.fixture
def owner_a(vendor_a: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor_a, role="owner").user


def table(content: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(content.decode("utf-8"))))


def test_the_owner_downloads_only_their_own_rows(
    client: Client, owner_a: User, vendor_b: Vendor
) -> None:
    verified_login(client, owner_a)
    response = client.get(SHOP_URL)
    assert response.status_code == 200
    body = response.content.decode()
    assert "mine-1" in body
    assert "mine-2" in body
    assert "theirs" not in body
    assert "other@example.com" not in body
    assert len(table(response.content)) == 1 + len(rows(LEGACY_A))


def test_the_download_headers(client: Client, owner_a: User) -> None:
    verified_login(client, owner_a)
    response = client.get(SHOP_URL)
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"].startswith("attachment; filename=")
    assert response["Content-Disposition"].endswith('.csv"')
    assert "no-store" in response["Cache-Control"]
    assert response["X-Content-Type-Options"] == "nosniff"


def test_the_download_is_audited_without_personal_data(client: Client, owner_a: User) -> None:
    verified_login(client, owner_a)
    client.get(SHOP_URL)
    entry = AuditLog.objects.get(action=DOWNLOADED)
    assert entry.actor_id == owner_a.pk
    assert (entry.after or {})["rows"] == len(rows(LEGACY_A))
    assert (entry.after or {})["on_behalf"] is False
    dump = f"{entry.actor_label} {entry.before} {entry.after} {entry.target_key}"
    for personal in ("Giulia", "giulia@example.com", "mine-1", owner_a.email):
        assert personal not in dump
    assert entry.created_at is not None


def test_nothing_is_created_in_the_new_system(client: Client, owner_a: User) -> None:
    before = (User.objects.count(), Vendor.objects.count())
    verified_login(client, owner_a)
    client.get(SHOP_URL)
    assert (User.objects.count(), Vendor.objects.count()) == before


@pytest.mark.parametrize("param", ["vendor", "vendor_id", "id"])
def test_naming_another_vendor_is_a_404_and_audited(
    client: Client, owner_a: User, vendor_b: Vendor, param: str
) -> None:
    verified_login(client, owner_a)
    response = client.get(SHOP_URL, {param: str(vendor_b.public_id)})
    assert response.status_code == 404
    assert "theirs" not in response.content.decode()
    assert not AuditLog.objects.filter(action=DOWNLOADED).exists()
    entry = AuditLog.objects.get(action=REFUSED)
    assert entry.actor_id == owner_a.pk
    assert "theirs" not in str(entry.after)


def test_naming_a_missing_vendor_looks_the_same(
    client: Client, owner_a: User, vendor_b: Vendor
) -> None:
    verified_login(client, owner_a)
    missing = client.get(SHOP_URL, {"vendor": "00000000-0000-0000-0000-000000000000"})
    other = client.get(SHOP_URL, {"vendor": str(vendor_b.public_id)})
    assert missing.status_code == other.status_code == 404
    assert missing.headers.keys() == other.headers.keys()
    assert "Vendor B" not in other.content.decode()
    assert "Shop B" not in other.content.decode()


def test_naming_the_own_vendor_is_fine(client: Client, owner_a: User, vendor_a: Vendor) -> None:
    verified_login(client, owner_a)
    assert client.get(SHOP_URL, {"vendor": str(vendor_a.public_id)}).status_code == 200


def test_a_member_of_two_vendors_gets_the_selected_one_only(
    client: Client, owner_a: User, vendor_b: Vendor
) -> None:
    VendorMemberFactory.create(vendor=vendor_b, user=owner_a, role="owner")
    verified_login(client, owner_a)
    session = client.session
    session[SESSION_KEY] = str(vendor_b.public_id)
    session.save()
    body = client.get(SHOP_URL).content.decode()
    assert "theirs a very long wait" in body  # B is the selected vendor: B's rows
    assert "mine-1" not in body
    assert AuditLog.objects.get(action=DOWNLOADED).target_key == str(vendor_b.public_id)


def test_vendor_staff_get_404(client: Client, vendor_a: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor_a, role="staff").user
    verified_login(client, staff)
    assert client.get(SHOP_URL).status_code == 404
    assert not AuditLog.objects.filter(action=DOWNLOADED).exists()


def test_a_vendor_without_a_legacy_id_gets_the_header_row_only(client: Client) -> None:
    vendor = VendorFactory.create(legacy_wp_user_id=None)
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    response = client.get(SHOP_URL)
    assert response.status_code == 200
    assert len(table(response.content)) == 1
    assert (AuditLog.objects.get(action=DOWNLOADED).after or {})["rows"] == 0


def test_formula_cells_are_neutralised_in_the_download(client: Client, owner_a: User) -> None:
    verified_login(client, owner_a)
    assert "'=HYPERLINK" in client.get(SHOP_URL).content.decode()


def test_an_anonymous_visitor_is_sent_to_the_login(client: Client) -> None:
    response = client.get(SHOP_URL)
    assert response.status_code == 302


# --- Staff on a vendor's behalf (platform space)


def platform_url(vendor: Vendor) -> str:
    return f"/manager/platform/vendors/{vendor.public_id}/legacy-export/"


def staff_with(role: str) -> User:
    from django.contrib.auth.models import Group

    user = UserFactory.create(staff=True)
    user.groups.add(Group.objects.get(name=roles.role(role).group))
    return User.objects.get(pk=user.pk)


@pytest.mark.parametrize("role", ["support", "owner"])
def test_staff_generate_the_file_for_a_vendor_and_it_is_audited(
    client: Client, vendor_a: Vendor, vendor_b: Vendor, role: str
) -> None:
    staff = staff_with(role)
    verified_login(client, staff)
    response = client.get(platform_url(vendor_a))
    assert response.status_code == 200
    body = response.content.decode()
    assert "mine-1" in body
    assert "theirs" not in body
    entry = AuditLog.objects.get(action=DOWNLOADED)
    assert entry.actor_id == staff.pk
    assert entry.target_key == str(vendor_a.public_id)
    assert (entry.after or {})["on_behalf"] is True
    assert (entry.after or {})["rows"] == len(rows(LEGACY_A))
    assert "no-store" in response["Cache-Control"]


@pytest.mark.parametrize("role", ["config", "catalog", "finance"])
def test_other_platform_roles_get_404(client: Client, vendor_a: Vendor, role: str) -> None:
    verified_login(client, staff_with(role))
    assert client.get(platform_url(vendor_a)).status_code == 404
    assert not AuditLog.objects.filter(action=DOWNLOADED).exists()


def test_a_vendor_owner_cannot_use_the_platform_url(
    client: Client, owner_a: User, vendor_a: Vendor
) -> None:
    verified_login(client, owner_a)
    # The gate refuses a vendor member in the platform space (403 page, no data).
    assert client.get(platform_url(vendor_a)).status_code in (403, 404)
    assert not AuditLog.objects.filter(action=DOWNLOADED).exists()


def test_staff_get_404_for_a_missing_vendor(client: Client) -> None:
    verified_login(client, staff_with("support"))
    url = "/manager/platform/vendors/00000000-0000-0000-0000-000000000000/legacy-export/"
    assert client.get(url).status_code == 404
