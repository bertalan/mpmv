"""CMP.5 part 4: the Manager pages of the termination notice. Platform space: staff give and
withdraw a notice (session level group 2, CORE.25). Shop space: the vendor sees the reason and the
effective date and can download its data during the notice period."""

import datetime as dt
import json
from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.utils import timezone

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.tests.factories import ProductFactory
from apps.compliance import services
from apps.compliance.models import TerminationNotice
from apps.compliance.tests.factories import TerminationNoticeFactory
from apps.core.models import AuditLog, OutboxEmail
from apps.core.tests.factories import PlatformOperatorFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

# The CORE.18 catalog role group (apps/manager/roles.py); not imported: compliance sits below.
CATALOG_GROUP = "Platform: catalog and moderation"

PLATFORM = "/manager/platform/terminations/"
SHOP = "/manager/shop/termination/"


@pytest.fixture(autouse=True)
def _operator() -> None:
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=timezone.now() - dt.timedelta(days=1)
    )


@pytest.fixture
def reviewer() -> Any:
    return UserFactory.create(
        permissions=["core.platform_owner", "compliance.review_notices"], platform_staff=True
    )


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create(status=Vendor.Status.ACTIVE, display_name="Shop One")


def catalog_reviewer() -> Any:
    user = UserFactory.create(
        platform_staff=True, is_staff=True, permissions=["compliance.review_notices"]
    )
    user.groups.add(Group.objects.get(name=CATALOG_GROUP))
    return user


def issue_form(vendor: Vendor, **extra: str) -> dict[str, str]:
    data = {
        "action": "issue",
        "vendor": str(vendor.public_id),
        "facts": "The storefront keeps selling goods the terms forbid.",
        "ground": "Platform terms, prohibited goods",
    }
    data.update(extra)
    return data


# --- Platform space


def test_a_reviewer_gives_notice_from_the_manager(
    client: Client, reviewer: Any, vendor: Vendor
) -> None:
    verified_login(client, reviewer)
    assert client.get(PLATFORM).status_code == 200
    response = client.post(PLATFORM, issue_form(vendor))
    assert response.status_code == 302
    notice = TerminationNotice.objects.get(vendor=vendor)
    assert notice.notice_days == 30
    assert notice.effective_at - notice.issued_at >= dt.timedelta(days=30)
    page = client.get(PLATFORM)
    assert "Shop One" in page.text
    assert client.get(f"{PLATFORM}{notice.public_id}/").status_code == 200


def test_a_date_inside_the_thirty_days_shows_an_error_and_stores_nothing(
    client: Client, reviewer: Any, vendor: Vendor
) -> None:
    verified_login(client, reviewer)
    soon = (timezone.localdate() + dt.timedelta(days=10)).isoformat()
    response = client.post(PLATFORM, issue_form(vendor, effective_on=soon))
    assert response.status_code == 200
    assert not TerminationNotice.objects.exists()


def test_an_exception_allows_a_near_date_from_the_form(
    client: Client, reviewer: Any, vendor: Vendor
) -> None:
    verified_login(client, reviewer)
    soon = (timezone.localdate() + dt.timedelta(days=3)).isoformat()
    response = client.post(
        PLATFORM, issue_form(vendor, effective_on=soon, exception="legal_obligation")
    )
    assert response.status_code == 302
    assert TerminationNotice.objects.get(vendor=vendor).exception == "legal_obligation"


def test_a_reviewer_withdraws_a_notice(client: Client, reviewer: Any, vendor: Vendor) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    verified_login(client, reviewer)
    url = f"{PLATFORM}{notice.public_id}/"
    response = client.post(url, {"action": "withdraw", "reason": "Corrected."})
    assert response.status_code == 302
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.WITHDRAWN
    assert OutboxEmail.objects.filter(template_key="compliance.termination_withdrawn").exists()


def test_a_withdrawal_without_a_reason_changes_nothing(
    client: Client, reviewer: Any, vendor: Vendor
) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    verified_login(client, reviewer)
    response = client.post(f"{PLATFORM}{notice.public_id}/", {"action": "withdraw", "reason": ""})
    assert response.status_code == 200
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.PENDING


def test_giving_notice_is_a_group_2_action_refused_without_an_open_window(
    client: Client, reviewer: Any, vendor: Vendor
) -> None:
    verified_login(client, reviewer, level=levels.NO_WINDOW)
    assert client.get(PLATFORM).status_code == 200  # reading is free
    response = client.post(PLATFORM, issue_form(vendor))
    assert response.status_code == 403
    assert not TerminationNotice.objects.exists()


def test_withdrawing_is_group_2_too(client: Client, reviewer: Any, vendor: Vendor) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    verified_login(client, reviewer, level=levels.NO_WINDOW)
    response = client.post(f"{PLATFORM}{notice.public_id}/", {"action": "withdraw", "reason": "x"})
    assert response.status_code == 403
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.PENDING


def test_staff_without_the_review_permission_and_vendor_members_get_404(
    client: Client, vendor: Vendor
) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    verified_login(client, UserFactory.create(permissions=["core.platform_owner"]))
    assert client.get(PLATFORM).status_code == 404
    assert client.get(f"{PLATFORM}{notice.public_id}/").status_code == 404
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    assert client.get(PLATFORM).status_code in (403, 404)  # the gate keeps members out


def test_an_unknown_notice_is_404(client: Client, reviewer: Any) -> None:
    verified_login(client, reviewer)
    assert client.get(f"{PLATFORM}00000000-0000-0000-0000-000000000000/").status_code == 404


# --- Shop space: the vendor during the notice period


def test_the_owner_sees_the_reason_and_the_date(client: Client, vendor: Vendor) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor, facts="Goods the terms forbid.")
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    page = client.get(SHOP)
    assert page.status_code == 200
    assert "Goods the terms forbid." in page.text
    assert timezone.localtime(notice.effective_at).strftime("%Y-%m-%d") in page.text


def test_without_a_notice_the_page_says_so(client: Client, vendor: Vendor) -> None:
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    page = client.get(SHOP)
    assert page.status_code == 200
    assert "no termination notice" in page.text.lower()


def test_another_vendors_notice_is_never_shown(client: Client, vendor: Vendor) -> None:
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    TerminationNoticeFactory.create(vendor=other, facts="Someone else's secret facts.")
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    assert "Someone else's secret facts." not in client.get(SHOP).text


def test_the_owner_downloads_its_data_during_the_notice_period(
    client: Client, vendor: Vendor
) -> None:
    TerminationNoticeFactory.create(vendor=vendor)
    mine = ProductFactory.create(vendor=vendor)
    theirs = ProductFactory.create()
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner, level=levels.NO_WINDOW)
    response = client.get(f"{SHOP}data/")
    assert response.status_code == 200
    assert "attachment" in response["Content-Disposition"]
    body = json.loads(b"".join(response.streaming_content))  # type: ignore[attr-defined]
    assert mine.sku in json.dumps(body)
    assert theirs.sku not in json.dumps(body)
    assert AuditLog.objects.filter(action="compliance.vendor_data.downloaded").exists()


def test_vendor_staff_members_see_the_notice_too(client: Client, vendor: Vendor) -> None:
    TerminationNoticeFactory.create(vendor=vendor)
    member = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, member)
    assert client.get(SHOP).status_code == 200
    assert client.get(f"{SHOP}data/").status_code == 200


def test_service_pending_lookup_matches_the_page(vendor: Vendor) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    assert services.pending_termination(vendor) == notice


def test_catalog_reviewers_read_but_cannot_give_or_withdraw(client: Client, vendor: Vendor) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    catalog = catalog_reviewer()
    verified_login(client, catalog)
    assert client.get(PLATFORM).status_code == 200
    assert client.get(f"{PLATFORM}{notice.public_id}/").status_code == 200
    assert client.post(PLATFORM, issue_form(vendor)).status_code == 404
    response = client.post(f"{PLATFORM}{notice.public_id}/", {"action": "withdraw", "reason": "x"})
    assert response.status_code == 404
    notice.refresh_from_db()
    assert notice.status == TerminationNotice.Status.PENDING


def test_the_owner_pages_hide_the_write_forms_from_catalog_reviewers(
    client: Client, vendor: Vendor
) -> None:
    notice = TerminationNoticeFactory.create(vendor=vendor)
    catalog = catalog_reviewer()
    verified_login(client, catalog)
    assert 'name="action" value="issue"' not in client.get(PLATFORM).text
    assert 'value="withdraw"' not in client.get(f"{PLATFORM}{notice.public_id}/").text


def test_the_download_needs_a_pending_notice(client: Client, vendor: Vendor) -> None:
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    assert client.get(f"{SHOP}data/").status_code == 404


def test_the_download_is_rate_limited_per_vendor(client: Client, vendor: Vendor) -> None:
    TerminationNoticeFactory.create(vendor=vendor)
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    for _ in range(services.DATA_DOWNLOADS_PER_HOUR):
        assert client.get(f"{SHOP}data/").status_code == 200
    limited = client.get(f"{SHOP}data/")
    assert limited.status_code == 429
    assert int(limited["Retry-After"]) > 0


def test_a_frozen_vendors_order_action_is_a_4xx_not_a_500(client: Client, vendor: Vendor) -> None:
    from apps.compliance import services
    from apps.core.audit import AuditContext
    from apps.orders.tests.factories import paid_vendor_order

    sub = paid_vendor_order(vendor)
    notice = TerminationNoticeFactory.create(vendor=vendor)
    services.apply_due_terminations(
        now=notice.effective_at, ctx=AuditContext(actor_label="system:test", source="system")
    )
    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    verified_login(client, owner)
    response = client.post(f"/manager/shop/orders/{sub.public_id}/accept/")
    assert 400 <= response.status_code < 500
    sub.refresh_from_db()
    assert sub.state == "pending"
