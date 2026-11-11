"""MIG.16: the migration choices form (shop space, owner) and the staff pages (platform space)."""

import datetime as dt
import re

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ingest import engine, services
from apps.ingest.models import MigrationChoice, MigrationRun
from apps.ingest.tests.factories import MigrationRunFactory
from apps.manager import roles
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import PLATFORM, SHOP

pytestmark = pytest.mark.django_db

PAGE = f"{SHOP}migration/"
PLATFORM_PAGE = f"{PLATFORM}migration/"
ALL_TYPES = list(engine.DATA_TYPES)


@pytest.fixture
def run() -> MigrationRun:
    return MigrationRunFactory.create(cutover_at=timezone.now() + dt.timedelta(days=60))


def form(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "period": "24",
        "data_types": ALL_TYPES,
        "login_window_months": "12",
    }
    data.update(overrides)
    return data


def window_options(html: str) -> list[int]:
    select = re.search(r'<select[^>]*name="login_window_months".*?</select>', html, re.S)
    assert select is not None
    return [int(v) for v in re.findall(r'<option value="(\d+)"', select.group(0))]


# --- The owner's form


def test_the_form_offers_exactly_the_allowed_values_and_states_the_floor(
    client: Client, owner: User, vendor: Vendor, run: MigrationRun
) -> None:
    verified_login(client, owner)
    page = client.get(PAGE).text
    assert window_options(page) == [1, 2, 3, 6, 12, 24, 36]
    assert re.findall(r'name="period"[^>]*value="([^"]+)"', page) == ["12", "24", "36", "all"]
    assert 'name="include_unpublished"' in page
    assert re.findall(r'name="data_types"[^>]*value="([^"]+)"', page) == ALL_TYPES
    assert "data-legal-floor" in page
    assert "data-restricted-archive" in page
    assert "restricted tax-only" in page
    assert f'href="{PAGE}"' in client.get(SHOP).text  # the menu entry


@pytest.mark.parametrize("window", engine.LOGIN_WINDOWS)
def test_each_login_window_is_saved_and_read_back_through_the_page(
    client: Client, owner: User, vendor: Vendor, run: MigrationRun, window: int
) -> None:
    verified_login(client, owner)
    response = client.post(PAGE, form(login_window_months=str(window)))
    assert response.status_code == 302
    stored = MigrationChoice.objects.get(vendor=vendor, run=run)
    assert (stored.login_window_months, stored.source, stored.decided_by_id) == (
        window,
        "vendor",
        owner.pk,
    )
    page = client.get(PAGE).text
    selected = re.search(r'<option value="(\d+)" selected', page)
    assert selected is not None
    assert int(selected.group(1)) == window


@pytest.mark.parametrize("window", ["0", "4", "5", "13", "48", "x", ""])
def test_any_other_login_window_is_refused(
    client: Client, owner: User, run: MigrationRun, window: str
) -> None:
    verified_login(client, owner)
    response = client.post(PAGE, form(login_window_months=window))
    assert response.status_code == 400
    assert 'class="error-summary"' in response.text
    assert not MigrationChoice.objects.exists()


def test_every_field_is_saved_and_read_back(
    client: Client, owner: User, vendor: Vendor, run: MigrationRun
) -> None:
    verified_login(client, owner)
    client.post(
        PAGE,
        form(
            period="all",
            data_types=["orders", "products"],
            include_unpublished="on",
            login_window_months="3",
        ),
    )
    stored = services.current_choice(vendor, run)
    assert stored is not None
    assert stored.values == engine.ChoiceValues(
        period_months=None,
        data_types=("orders", "products"),
        include_unpublished=True,
        login_window_months=3,
    )
    page = client.get(PAGE).text
    assert re.search(r'value="all"[^>]*checked', page)
    assert re.search(r'name="include_unpublished"[^>]*checked', page)
    assert re.search(r'name="data_types"[^>]*value="products"[^>]*checked', page)
    assert not re.search(r'name="data_types"[^>]*value="reviews"[^>]*checked', page)


def test_an_unknown_period_or_data_type_or_customers_without_orders_is_refused(
    client: Client, owner: User, run: MigrationRun
) -> None:
    verified_login(client, owner)
    assert client.post(PAGE, form(period="6")).status_code == 400
    assert client.post(PAGE, form(data_types=["orders", "passwords"])).status_code == 400
    assert client.post(PAGE, form(data_types=["customers"])).status_code == 400
    assert not MigrationChoice.objects.exists()


def test_a_change_is_audited_with_the_old_and_new_value(
    client: Client, owner: User, vendor: Vendor, run: MigrationRun
) -> None:
    verified_login(client, owner)
    client.post(PAGE, form(login_window_months="6"))
    client.post(PAGE, form(login_window_months="36"))
    entry = AuditLog.objects.filter(action=services.MIGRATION_CHOICE_RECORDED).order_by("id").last()
    assert entry is not None
    assert entry.before is not None
    assert entry.after is not None
    assert (entry.before["login_window_months"], entry.after["login_window_months"]) == (6, 36)
    assert entry.actor_id == owner.pk


def test_vendor_staff_get_404_and_no_menu_entry(
    client: Client, vendor: Vendor, run: MigrationRun
) -> None:
    member = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, member)
    assert client.get(PAGE).status_code == 404
    assert client.post(PAGE, form()).status_code == 404
    assert PAGE not in client.get(SHOP).text
    assert not MigrationChoice.objects.exists()


def test_another_vendors_choice_is_a_404_and_is_audited(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor, run: MigrationRun
) -> None:
    verified_login(client, owner)
    assert client.get(f"{PAGE}?vendor={other_vendor.public_id}").status_code == 404
    assert client.post(PAGE, {**form(), "vendor": str(other_vendor.public_id)}).status_code == 404
    assert not MigrationChoice.objects.exists()
    entries = AuditLog.objects.filter(action=services.MIGRATION_CHOICE_DENIED)
    assert entries.count() == 2
    first = entries.first()
    assert first is not None
    assert first.after is not None
    assert first.after["requested_vendor"] == str(other_vendor.public_id)
    assert first.vendor_key == str(vendor.public_id)
    assert first.actor_id == owner.pk
    # Its own id is fine.
    assert client.get(f"{PAGE}?vendor={vendor.public_id}").status_code == 200
    assert entries.count() == 2


def test_after_the_deadline_the_owner_cannot_change_the_choice(
    client: Client, owner: User, vendor: Vendor
) -> None:
    MigrationRunFactory.create(cutover_at=timezone.now() + dt.timedelta(days=5))  # due 9 days ago
    verified_login(client, owner)
    page = client.get(PAGE)
    assert page.status_code == 200
    assert 'name="login_window_months"' not in page.text
    assert client.post(PAGE, form()).status_code == 403
    assert not MigrationChoice.objects.exists()


def test_without_a_planned_migration_the_page_says_so_and_saves_nothing(
    client: Client, owner: User
) -> None:
    verified_login(client, owner)
    assert "No migration is scheduled" in client.get(PAGE).text
    assert client.post(PAGE, form()).status_code == 404


def test_the_page_shows_the_defaults_when_nothing_was_chosen(
    client: Client, owner: User, run: MigrationRun
) -> None:
    verified_login(client, owner)
    page = client.get(PAGE).text
    assert "Not chosen yet" in page
    assert re.search(r'<option value="12" selected', page)
    assert re.search(r'name="period"[^>]*value="24"[^>]*checked', page)


# --- Platform space


def _with_role(key: str) -> User:
    user = UserFactory.create(staff=True)
    user.groups.add(Group.objects.get(name=roles.role(key).group))
    return User.objects.get(pk=user.pk)


def test_staff_see_every_vendors_choice(
    client: Client, platform_staff: User, vendor: Vendor, other_vendor: Vendor, run: MigrationRun
) -> None:
    services.record_choice(
        other_vendor,
        run,
        engine.DEFAULT_CHOICE,
        source="vendor",
        ctx=AuditContext(actor_label="user:x", source="vendor"),
    )
    verified_login(client, platform_staff)
    page = client.get(PLATFORM_PAGE).text
    assert vendor.display_name in page
    assert other_vendor.display_name in page
    assert "Not chosen" in page
    assert f'href="{PLATFORM_PAGE}"' in client.get(PLATFORM).text


def test_the_owner_enters_a_choice_on_a_vendors_behalf_with_audit(
    client: Client, platform_staff: User, vendor: Vendor, run: MigrationRun
) -> None:
    verified_login(client, platform_staff)
    url = f"{PLATFORM_PAGE}{vendor.public_id}/"
    assert client.get(url).status_code == 200
    response = client.post(url, {**form(login_window_months="2"), "vendor": str(vendor.public_id)})
    assert response.status_code == 302
    stored = services.current_choice(vendor, run)
    assert stored is not None
    assert (stored.login_window_months, stored.source, stored.decided_by_id) == (
        2,
        "staff",
        platform_staff.pk,
    )
    entry = AuditLog.objects.get(action=services.MIGRATION_CHOICE_RECORDED)
    assert entry.after is not None
    assert entry.after["source"] == "staff"
    assert entry.vendor_key == str(vendor.public_id)


def test_staff_can_enter_a_choice_after_the_vendor_deadline(
    client: Client, platform_staff: User, vendor: Vendor
) -> None:
    run = MigrationRunFactory.create(cutover_at=timezone.now() + dt.timedelta(days=5))
    verified_login(client, platform_staff)
    response = client.post(f"{PLATFORM_PAGE}{vendor.public_id}/", form(period="12"))
    assert response.status_code == 302
    assert services.current_choice(vendor, run).period_months == 12  # type: ignore[union-attr]


def test_a_read_only_role_sees_but_cannot_enter_a_choice(
    client: Client, vendor: Vendor, run: MigrationRun
) -> None:
    verified_login(client, _with_role(roles.CATALOG))
    url = f"{PLATFORM_PAGE}{vendor.public_id}/"
    assert client.get(PLATFORM_PAGE).status_code == 200
    page = client.get(url)
    assert page.status_code == 200
    assert 'name="login_window_months"' not in page.text
    assert client.post(url, form()).status_code == 404
    assert not MigrationChoice.objects.exists()


def test_an_unknown_vendor_is_a_404_for_staff(client: Client, platform_staff: User) -> None:
    verified_login(client, platform_staff)
    nil = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"{PLATFORM_PAGE}{nil}/").status_code == 404


def test_a_vendor_member_is_refused_on_the_platform_pages(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    assert client.get(PLATFORM_PAGE).status_code == 403  # the space gate (CORE.17)
    assert client.get(f"{PLATFORM_PAGE}{vendor.public_id}/").status_code == 403


def test_support_enters_a_choice_on_a_vendors_behalf_with_audit(
    client: Client, vendor: Vendor, run: MigrationRun
) -> None:
    support = _with_role(roles.SUPPORT)
    verified_login(client, support)
    response = client.post(f"{PLATFORM_PAGE}{vendor.public_id}/", form(login_window_months="1"))
    assert response.status_code == 302
    stored = services.current_choice(vendor, run)
    assert stored is not None
    assert (stored.login_window_months, stored.source, stored.decided_by_id) == (
        1,
        "staff",
        support.pk,
    )
    assert AuditLog.objects.filter(
        action=services.MIGRATION_CHOICE_RECORDED, actor_id=support.pk
    ).exists()
