"""VEN.9: the vendor money pages of the shop space: balance, transactions, payouts, period
statement and audited CSV export. Isolation (own ledger only), roles, filters, pagination."""

import csv
import datetime as dt
import io
import secrets
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ledger import engine, services
from apps.ledger.models import LedgerEntry
from apps.ledger.tests.factories import _open_operator
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import SHOP

pytestmark = pytest.mark.django_db

MONEY = f"{SHOP}money/"
CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def add(vendor: Vendor, kind: str, amount: str, memo: str = "") -> LedgerEntry:
    key = secrets.token_urlsafe(8)
    services.record_entry(
        vendor,
        kind=kind,
        amount=Decimal(amount),
        idempotency_key=key,
        ctx=CTX,
        memo=memo,
        platform_entity=_open_operator(),
    )
    return LedgerEntry.objects.get(idempotency_key=key)


def test_the_owner_sees_balance_and_payouts_and_a_menu_entry(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "100.00", "Order one")
    add(vendor, "commission", "-10.00")
    add(vendor, "payout", "-50.00", "June payout")
    verified_login(client, owner)
    response = client.get(MONEY)
    assert response.status_code == 200
    assert "data-balance>40,00<" in response.text or "data-balance>40.00<" in response.text
    assert "June payout" in response.text
    assert f'href="{MONEY}"' in client.get(SHOP).text


def test_staff_get_404_everywhere_and_no_menu_entry(client: Client, vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, staff)
    for tail in ("", "transactions/", "payouts/", "statement/"):
        assert client.get(f"{MONEY}{tail}").status_code == 404
    assert client.post(f"{MONEY}export/").status_code == 404
    assert f'href="{MONEY}"' not in client.get(SHOP).text


def test_another_vendors_entries_never_show(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    add(vendor, "sale", "10.00", "mine-marker")
    add(other_vendor, "sale", "99.00", "theirs-marker")
    add(other_vendor, "payout", "-5.00", "theirs-payout")
    verified_login(client, owner)
    for tail in ("", "transactions/", "payouts/", "statement/"):
        text = client.get(f"{MONEY}{tail}").text
        assert "theirs-marker" not in text
        assert "theirs-payout" not in text
    body = client.post(f"{MONEY}export/").content.decode()
    assert "mine-marker" in body
    assert "theirs-marker" not in body
    assert "99.00" not in body


def test_transactions_show_kind_labels_signed_amounts_and_running_balance(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "100.00")
    add(vendor, "commission", "-12.50")
    verified_login(client, owner)
    text = client.get(f"{MONEY}transactions/").text
    assert "Sale" in text
    assert "Commission" in text
    assert "+100" in text
    assert "-12" in text
    assert "87" in text  # the running balance after the commission


def test_transactions_are_paginated_and_filtered_by_kind_and_period(
    client: Client, owner: User, vendor: Vendor
) -> None:
    for i in range(30):
        add(vendor, "sale", "1.00", f"row-{i:02d}")
    add(vendor, "refund", "-2.00", "a-refund")
    verified_login(client, owner)
    first = client.get(f"{MONEY}transactions/").text
    assert "a-refund" in first
    assert "row-00" not in first  # on page two
    assert "row-00" in client.get(f"{MONEY}transactions/?page=2").text
    only_refunds = client.get(f"{MONEY}transactions/?kind=refund").text
    assert "a-refund" in only_refunds
    assert "row-29" not in only_refunds
    today = timezone.localdate().isoformat()
    assert "a-refund" in client.get(f"{MONEY}transactions/?from={today}&to={today}").text
    past = (timezone.localdate() - dt.timedelta(days=40)).isoformat()
    assert "a-refund" not in client.get(f"{MONEY}transactions/?from={past}&to={past}").text


def test_an_invalid_date_is_reported_and_ignored(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "1.00", "kept")
    verified_login(client, owner)
    text = client.get(f"{MONEY}transactions/?from=nope").text
    assert "kept" in text
    assert 'role="alert"' in text


def test_payouts_list_only_payout_and_transfer(client: Client, owner: User, vendor: Vendor) -> None:
    add(vendor, "sale", "100.00", "a-sale")
    add(vendor, "payout", "-10.00", "a-payout")
    add(vendor, "transfer", "-20.00", "a-transfer")
    verified_login(client, owner)
    text = client.get(f"{MONEY}payouts/").text
    assert "a-payout" in text
    assert "a-transfer" in text
    assert "a-sale" not in text


def test_the_statement_comes_from_vendor_statement(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "100.00")
    add(vendor, "refund", "-30.00")
    verified_login(client, owner)
    response = client.get(f"{MONEY}statement/")
    assert response.status_code == 200
    statement = response.context["statement"]
    assert (statement.opening, statement.credits, statement.debits, statement.closing) == (
        Decimal("0.00"),
        Decimal("100.00"),
        Decimal("-30.00"),
        Decimal("70.00"),
    )
    assert "Refund" in response.text


def test_csv_export_is_bounded_safe_and_audited(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "100.00", '=HYPERLINK("http://x")')
    add(vendor, "adjustment", "-3.00", "-2+3")
    verified_login(client, owner)
    response = client.post(f"{MONEY}export/", {"kind": ""})
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment" in response["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0] == list(services.CSV_HEADER)
    memos = {r[7] for r in rows[1:]}
    assert memos == {'\'=HYPERLINK("http://x")', "'-2+3"}
    amounts = {r[2] for r in rows[1:]}
    assert amounts == {"+100.00", "-3.00"}  # numbers, not escaped
    log = AuditLog.objects.get(action="ledger.export_csv")
    assert log.actor_id == owner.pk
    assert log.vendor_key == str(vendor.public_id)
    assert isinstance(log.after, dict)
    assert log.after["rows"] == 2
    assert "HYPERLINK" not in str(log.after)


def test_csv_export_needs_post_and_a_csrf_token(vendor: Vendor, owner: User) -> None:
    csrf_client = Client(enforce_csrf_checks=True)
    verified_login(csrf_client, owner)
    assert csrf_client.get(f"{MONEY}export/").status_code == 405
    assert csrf_client.post(f"{MONEY}export/").status_code == 403


def test_every_kind_has_a_translatable_label() -> None:
    from apps.manager.money_views import KIND_LABELS

    assert set(KIND_LABELS) == set(engine.KINDS)
