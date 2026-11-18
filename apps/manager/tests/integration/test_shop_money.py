"""VEN.9: the vendor money pages of the shop space: balance, transactions, payouts, period
statement and the audited CSV ledger extract.

Done when: the vendor sees only its own transactions. Also: owner only (staff 404), translated
kind labels, signed amounts and running balance, filters by period and kind, pagination, the
statement from `vendor_statement`, the non-fiscal label with its reference, a formula-safe CSV,
memos never shown, and nothing written to the ledger by any page.
"""

import csv
import datetime as dt
import io
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone, translation

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.ledger import engine, services
from apps.ledger.models import LedgerEntry
from apps.ledger.tests.factories import _open_operator
from apps.manager.money_views import KIND_LABELS
from apps.manager.vendor_choice import SESSION_KEY
from apps.orders.models import VendorOrder
from apps.orders.tests.factories import VendorOrderFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory

from .conftest import SHOP
from .test_shop_owner_passkey import _do_it_once, _enrol_passkey, _open_window

pytestmark = pytest.mark.django_db

MONEY = f"{SHOP}money/"
PAGES = ("", "transactions/", "payouts/", "statement/")
CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)
# Periods are cut in the business time zone (settings.BUSINESS_TIME_ZONE), not in UTC.
ROME = ZoneInfo(settings.BUSINESS_TIME_ZONE)


def add(
    vendor: Vendor,
    kind: str,
    amount: str,
    *,
    ref: str = "",
    memo: str = "",
    vendor_order: VendorOrder | None = None,
) -> LedgerEntry:
    key = secrets.token_urlsafe(8)
    services.record_entry(
        vendor,
        kind=kind,
        amount=Decimal(amount),
        idempotency_key=key,
        ctx=CTX,
        external_ref=ref,
        memo=memo,
        vendor_order=vendor_order,
        platform_entity=_open_operator(),
    )
    return LedgerEntry.objects.get(idempotency_key=key)


@contextmanager
def at(monkeypatch: pytest.MonkeyPatch, when: dt.datetime) -> Iterator[None]:
    """Entries created inside get `created_at = when` (the table is append-only: no backdating)."""
    with monkeypatch.context() as patch:
        patch.setattr(timezone, "now", lambda: when)
        yield


def export(client: Client, **data: str) -> list[list[str]]:
    response = client.post(f"{MONEY}export/", data)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    return list(csv.reader(io.StringIO(response.content.decode())))


def test_every_kind_has_a_translated_label() -> None:
    assert set(KIND_LABELS) == engine.KINDS
    with translation.override("it"):
        assert str(KIND_LABELS[engine.SALE]) != "Sale"
        assert str(KIND_LABELS[engine.PAYOUT]) != "Payout"


def test_the_owner_sees_balance_latest_payouts_and_a_menu_entry(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "100.00")
    add(vendor, "commission", "-10.00")
    add(vendor, "payout", "-50.00", ref="po_june")
    verified_login(client, owner)
    response = client.get(MONEY)
    assert response.status_code == 200
    assert response.context["balance"] == Decimal("40.00")
    assert [r["reference"] for r in response.context["recent_payouts"]] == ["po_june"]
    assert len(response.context["recent"]) == 3
    assert "po_june" in response.text
    assert f'href="{MONEY}"' in client.get(SHOP).text


def test_staff_get_404_everywhere_and_no_menu_entry(client: Client, vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, staff)
    for tail in PAGES:
        assert client.get(f"{MONEY}{tail}").status_code == 404
    assert client.post(f"{MONEY}export/").status_code == 404
    assert f'href="{MONEY}"' not in client.get(SHOP).text
    assert not AuditLog.objects.filter(action=services.EXPORT_CSV).exists()


def test_the_vendor_sees_only_its_own_transactions(
    client: Client, owner: User, vendor: Vendor, other_vendor: Vendor
) -> None:
    add(vendor, "sale", "10.00", ref="tr_mine")
    add(other_vendor, "sale", "99.00", ref="tr_theirs")
    add(other_vendor, "payout", "-5.00", ref="po_theirs")
    verified_login(client, owner)
    for tail in PAGES:
        text = client.get(f"{MONEY}{tail}").text
        assert "tr_theirs" not in text
        assert "po_theirs" not in text
    assert client.get(MONEY).context["balance"] == Decimal("10.00")
    rows = export(client)
    body = "\n".join(",".join(r) for r in rows)
    assert "tr_mine" in body
    assert "tr_theirs" not in body
    assert "99.00" not in body
    statement = client.get(f"{MONEY}statement/").context["statement"]
    assert statement.closing == Decimal("10.00")


def test_a_forged_vendor_in_the_session_is_a_404(
    client: Client, owner: User, other_vendor: Vendor
) -> None:
    add(other_vendor, "sale", "99.00", ref="tr_theirs")
    verified_login(client, owner)
    session = client.session
    session[SESSION_KEY] = str(other_vendor.public_id)
    session.save()
    response = client.get(f"{MONEY}transactions/")
    assert response.status_code == 404
    assert "tr_theirs" not in response.text


def test_memos_are_never_shown_or_exported(client: Client, owner: User, vendor: Vendor) -> None:
    add(vendor, "adjustment", "5.00", ref="adj_ref", memo="internal-note-marker")
    verified_login(client, owner)
    for tail in PAGES:
        assert "internal-note-marker" not in client.get(f"{MONEY}{tail}").text
    rows = export(client)
    assert "internal-note-marker" not in str(rows)
    assert "memo" not in rows[1]


def test_transactions_show_labels_signed_amounts_running_balance_and_order_link(
    client: Client, owner: User, vendor: Vendor
) -> None:
    sub = VendorOrderFactory.create(vendor=vendor)
    add(vendor, "sale", "100.00", vendor_order=sub)
    add(vendor, "commission", "-12.50", vendor_order=sub)
    verified_login(client, owner)
    response = client.get(f"{MONEY}transactions/")
    rows = response.context["rows"]
    assert [(r["kind"], r["amount"], r["balance_after"]) for r in rows] == [
        ("Commission", Decimal("-12.50"), Decimal("87.50")),
        ("Sale", Decimal("100.00"), Decimal("100.00")),
    ]
    assert f"/manager/shop/orders/{sub.public_id}/" in response.text
    assert "+100" in response.text
    assert "-12" in response.text


def test_transactions_are_paginated_and_filtered_by_kind(
    client: Client, owner: User, vendor: Vendor
) -> None:
    for i in range(30):
        add(vendor, "sale", "1.00", ref=f"row-{i:02d}")
    add(vendor, "refund", "-2.00", ref="a-refund")
    verified_login(client, owner)
    first = client.get(f"{MONEY}transactions/")
    assert "a-refund" in first.text
    assert "row-00" not in first.text
    assert "kind=" not in first.context["next_url"]
    assert "row-00" in client.get(f"{MONEY}transactions/{first.context['next_url']}").text
    only = client.get(f"{MONEY}transactions/?kind=refund").context["rows"]
    assert [r["reference"] for r in only] == ["a-refund"]
    unknown = client.get(f"{MONEY}transactions/?kind=bogus").context["rows"]
    assert len(unknown) == 25  # an unknown kind is ignored


def test_transactions_and_payouts_are_filtered_by_period(
    client: Client, owner: User, vendor: Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    tz = ROME
    with at(monkeypatch, dt.datetime(2026, 1, 15, 12, tzinfo=tz)):
        add(vendor, "sale", "10.00", ref="jan-sale")
        add(vendor, "payout", "-10.00", ref="jan-payout")
    with at(monkeypatch, dt.datetime(2026, 2, 15, 12, tzinfo=tz)):
        add(vendor, "sale", "20.00", ref="feb-sale")
        add(vendor, "payout", "-5.00", ref="feb-payout")
    verified_login(client, owner)
    feb = "?from=2026-02-01&to=2026-02-28"
    refs = [r["reference"] for r in client.get(f"{MONEY}transactions/{feb}").context["rows"]]
    assert refs == ["feb-payout", "feb-sale"]
    payouts = client.get(f"{MONEY}payouts/{feb}").context["rows"]
    assert [r["reference"] for r in payouts] == ["feb-payout"]
    all_payouts = client.get(f"{MONEY}payouts/").context["rows"]
    assert [r["reference"] for r in all_payouts] == ["feb-payout", "jan-payout"]
    reversed_period = client.get(f"{MONEY}transactions/?from=2026-02-28&to=2026-02-01")
    assert len(reversed_period.context["rows"]) == 4
    assert reversed_period.context["filter_error"]


def test_the_statement_uses_the_ledger_statement_with_label_and_reference(
    client: Client, owner: User, vendor: Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    tz = ROME
    with at(monkeypatch, dt.datetime(2026, 1, 15, 12, tzinfo=tz)):
        add(vendor, "sale", "10.00")
    with at(monkeypatch, dt.datetime(2026, 2, 15, 12, tzinfo=tz)):
        add(vendor, "sale", "20.00")
        add(vendor, "commission", "-2.00")
        add(vendor, "payout", "-25.00")
    verified_login(client, owner)
    response = client.get(f"{MONEY}statement/?from=2026-02-01&to=2026-02-28")
    start = dt.datetime(2026, 2, 1, tzinfo=tz)
    end = dt.datetime(2026, 3, 1, tzinfo=tz)
    assert response.context["statement"] == services.vendor_statement(vendor, start, end)
    statement = response.context["statement"]
    assert (statement.opening, statement.credits, statement.debits, statement.closing) == (
        Decimal("10.00"),
        Decimal("20.00"),
        Decimal("-27.00"),
        Decimal("3.00"),
    )
    reference = engine.extract_reference(
        str(vendor.public_id), dt.date(2026, 2, 1), dt.date(2026, 2, 28)
    )
    assert reference in response.text
    assert engine.NON_FISCAL_LABEL in response.text


def test_the_statement_defaults_to_the_current_month(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    filters = client.get(f"{MONEY}statement/?from=2026-01-01").context["filters"]
    assert (filters.first, filters.last) == engine.month_bounds(timezone.localdate())


def test_the_export_is_a_safe_bounded_audited_non_fiscal_csv(
    client: Client, owner: User, vendor: Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    tz = ROME
    sub = VendorOrderFactory.create(vendor=vendor)
    with at(monkeypatch, dt.datetime(2026, 1, 15, 12, tzinfo=tz)):
        add(vendor, "sale", "1.00", ref="jan-only")
    with at(monkeypatch, dt.datetime(2026, 2, 10, 12, tzinfo=tz)):
        add(vendor, "sale", "100.00", ref="=HYPERLINK(1)", vendor_order=sub)
        add(vendor, "commission", "-12.50", ref="@SUM(A1)")
    verified_login(client, owner)
    entries_before = LedgerEntry.objects.count()
    rows = export(client, **{"from": "2026-02-01", "to": "2026-02-28"})
    reference = engine.extract_reference(
        str(vendor.public_id), dt.date(2026, 2, 1), dt.date(2026, 2, 28)
    )
    assert rows[0] == [engine.NON_FISCAL_LABEL, reference]
    assert rows[1] == list(services.CSV_HEADER)
    body = rows[2:]
    assert [r[1:5] for r in body] == [
        ["commission", "-12.50", "EUR", "+88.50"],  # the running balance counts January too
        ["sale", "+100.00", "EUR", "+101.00"],
    ]
    assert body[0][6] == "'@SUM(A1)"
    assert body[1][6] == "'=HYPERLINK(1)"
    assert body[1][5] == sub.order.number
    assert "jan-only" not in str(rows)
    assert LedgerEntry.objects.count() == entries_before  # read only
    log = AuditLog.objects.get(action=services.EXPORT_CSV)
    assert log.after == {
        "reference": reference,
        "rows": 2,
        "from": "2026-02-01",
        "to": "2026-02-28",
        "kinds": None,
    }
    assert log.vendor_key == str(vendor.public_id)
    assert log.source == AuditLog.Source.VENDOR


def test_the_export_defaults_to_the_current_month_and_keeps_the_kind(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "3.00")
    add(vendor, "refund", "-1.00")
    verified_login(client, owner)
    rows = export(client, kind="refund")
    first, last = engine.month_bounds(timezone.localdate())
    assert rows[0][1] == engine.extract_reference(str(vendor.public_id), first, last)
    assert [r[1] for r in rows[2:]] == ["refund"]


def test_the_export_is_post_only_and_csrf_checked(
    owner: User, vendor: Vendor, client: Client
) -> None:
    verified_login(client, owner)
    assert client.get(f"{MONEY}export/").status_code == 405
    strict = Client(enforce_csrf_checks=True)
    verified_login(strict, owner)
    assert strict.post(f"{MONEY}export/").status_code == 403
    assert not AuditLog.objects.filter(action=services.EXPORT_CSV).exists()


def test_the_pages_write_nothing_to_the_ledger(client: Client, owner: User, vendor: Vendor) -> None:
    add(vendor, "sale", "5.00")
    verified_login(client, owner)
    before = list(LedgerEntry.objects.values_list("id", "amount", "balance_after"))
    for tail in PAGES:
        assert client.get(f"{MONEY}{tail}").status_code == 200
    assert list(LedgerEntry.objects.values_list("id", "amount", "balance_after")) == before


# The export is a sensitive action (CORE.25 group 2, `export`): the shop owner confirms it with
# its passkey, once or in a window scoped to its shop. `verified_login`'s default window hides
# the gate, so these tests sign in as a real login does (no window).


def test_after_a_real_login_the_export_asks_for_a_passkey_and_exports_nothing(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "1.00", ref="tr_gated")
    _enrol_passkey(owner)
    verified_login(client, owner, level=levels.NO_WINDOW)
    refused = client.post(f"{MONEY}export/")
    assert refused.status_code == 403
    assert refused["Content-Type"].startswith("text/html")
    assert refused.context["action"] == "export"
    assert "Do it once" in refused.content.decode()
    assert "tr_gated" not in refused.content.decode()
    assert not AuditLog.objects.filter(action=services.EXPORT_CSV).exists()


def test_do_it_once_exports_once_and_a_shop_window_exports_again(
    client: Client, owner: User, vendor: Vendor
) -> None:
    add(vendor, "sale", "1.00", ref="tr_gated")
    authenticator = _enrol_passkey(owner)
    verified_login(client, owner, level=levels.NO_WINDOW)
    refused = client.post(f"{MONEY}export/")
    assert _do_it_once(client, authenticator, refused).status_code == 200
    assert "tr_gated" in str(export(client))
    assert client.post(f"{MONEY}export/").status_code == 403  # once means once
    assert _open_window(client, authenticator).status_code == 200
    assert "tr_gated" in str(export(client))
    assert "tr_gated" in str(export(client))  # repeated inside the window
    assert AuditLog.objects.filter(action=services.EXPORT_CSV).count() == 3


# Regression (review of #743): 00:30 in Rome on 1 March is 23:30 UTC on 28 February. The
# periods are the vendor's (business) calendar days, so the entry belongs to March everywhere.
LATE_FEB_UTC = dt.datetime(2026, 2, 28, 23, 30, tzinfo=dt.UTC)
MARCH = {"from": "2026-03-01", "to": "2026-03-31"}
FEBRUARY = {"from": "2026-02-01", "to": "2026-02-28"}


def test_an_entry_at_half_past_midnight_in_rome_on_the_1st_is_in_that_month(
    client: Client, owner: User, vendor: Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    with at(monkeypatch, dt.datetime(2026, 2, 10, 12, tzinfo=dt.UTC)):
        add(vendor, "sale", "10.00", ref="feb-sale")
    with at(monkeypatch, LATE_FEB_UTC):
        add(vendor, "sale", "5.00", ref="march-first-sale")
    verified_login(client, owner)

    march = client.get(f"{MONEY}statement/", MARCH).context["statement"]
    assert (march.opening, march.credits, march.closing) == (
        Decimal("10.00"),
        Decimal("5.00"),
        Decimal("15.00"),
    )
    february = client.get(f"{MONEY}statement/", FEBRUARY).context["statement"]
    assert (february.opening, february.credits, february.closing) == (
        Decimal("0.00"),
        Decimal("10.00"),
        Decimal("10.00"),
    )

    def refs(period: dict[str, str]) -> list[str]:
        rows = client.get(f"{MONEY}transactions/", period).context["rows"]
        return [r["reference"] for r in rows]

    assert refs(MARCH) == ["march-first-sale"]
    assert refs(FEBRUARY) == ["feb-sale"]

    rows = export(client, **MARCH)
    assert [(r[0], r[6]) for r in rows[2:]] == [("2026-03-01T00:30:00+01:00", "march-first-sale")]
    assert "feb-sale" not in str(export(client, **MARCH))
    assert "march-first-sale" not in str(export(client, **FEBRUARY))

    page = client.get(f"{MONEY}transactions/", MARCH).text
    assert 'datetime="2026-03-01T00:30:00+01:00"' in page


def test_the_default_month_is_the_business_month(
    client: Client, owner: User, vendor: Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified_login(client, owner)
    with at(monkeypatch, LATE_FEB_UTC):  # already 1 March in Rome
        filters = client.get(f"{MONEY}statement/").context["filters"]
    assert (filters.first, filters.last) == (dt.date(2026, 3, 1), dt.date(2026, 3, 31))
