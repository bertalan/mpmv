"""PAY.4: the Manager page of the open stray-payment alerts and their manual close.

Support and the platform owner see the list and close an alert with a reason (CSRF, audited
through `payments.stray`); Finance reads the list and its close answers 403; Catalog, a vendor
member and a customer get 404. A closed alert leaves the list and the nightly job reminds no
more. Payments and access-control code: needs human review.
"""

import datetime as dt
import secrets
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.formats import localize
from django.utils.html import escape
from django.utils.translation import gettext, override

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.payments import registry, stray
from apps.payments.changeset_keys import (
    PAYMENT_STRAY_CHECKED,
    PAYMENT_STRAY_CLOSED,
    PAYMENT_STRAY_REMINDED,
)
from apps.payments.models import Payment
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

PAGE = "/manager/platform/stray-payments/"
CTX = AuditContext(actor_label="test", source=AuditLog.Source.SYSTEM)
REASON = "Refunded by hand in the provider dashboard."


def flag(reason: str = "late", amount: str = "12.50") -> Payment:
    ref = f"pi_{secrets.token_hex(6)}"
    payment = Payment.objects.create(
        provider="fake",
        provider_ref=ref,
        order_ref=f"ORD-{secrets.token_hex(3)}",
        amount=Decimal(amount),
        currency="EUR",
        succeeded_at=timezone.now(),
    )
    stray.flag_stray_capture(payment, reason=reason, ctx=CTX)
    return payment


def shown(text: str, page: str) -> bool:
    """`text`, in the primary language, is on `page` (raw or HTML-escaped)."""
    with override(settings.LANGUAGE_CODE):
        local = gettext(text)
    return local in page or escape(local) in page


def staff(role: str | None) -> tuple[Client, User]:
    user = UserFactory.create(platform_role=role) if role else UserFactory.create()
    client = Client()
    verified_login(client, user)
    return client, user


@pytest.mark.parametrize("role", ["support", "owner", "finance"])
def test_the_roles_with_the_row_see_the_open_alerts(role: str) -> None:
    payment = flag(reason="unmatched")
    client, _user = staff(role)
    page = client.get(PAGE)
    assert page.status_code == 200
    text = page.content.decode()
    assert payment.provider_ref in text
    with override(settings.LANGUAGE_CODE):
        assert f"{localize(payment.amount)} EUR" in text
    assert shown("No matching order", text)
    assert shown("Close the alert", text) is (role != "finance")


@pytest.mark.parametrize("role", ["catalog", "config"])
def test_other_platform_roles_get_404(role: str) -> None:
    flag()
    client, _user = staff(role)
    assert client.get(PAGE).status_code == 404
    response = client.post(PAGE, {"payment": "pi_x", "reason": REASON})
    assert response.status_code == 404
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()


@pytest.mark.parametrize("who", ["customer", "vendor"])
def test_a_customer_or_vendor_member_gets_the_platform_gates_refusal(who: str) -> None:
    payment = flag()
    user = (
        VendorMemberFactory.create(role="owner").user if who == "vendor" else UserFactory.create()
    )
    client = Client()
    verified_login(client, user)
    assert client.get(PAGE).status_code in (403, 404)
    client.post(PAGE, {"payment": payment.provider_ref, "reason": REASON})
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()


def test_finance_reads_but_a_close_answers_403() -> None:
    payment = flag()
    client, _user = staff("finance")
    response = client.post(PAGE, {"payment": payment.provider_ref, "reason": REASON})
    assert response.status_code == 403
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()


def test_support_closes_an_alert_it_is_audited_and_leaves_the_list() -> None:
    payment = flag()
    client, support = staff("support")
    response = client.post(PAGE, {"payment": payment.provider_ref, "reason": REASON})
    assert response.status_code == 302
    entry = AuditLog.objects.get(action=PAYMENT_STRAY_CLOSED, target_key=payment.provider_ref)
    assert entry.actor_id == support.pk
    assert (entry.after or {})["by"] == "support"
    assert REASON not in str(entry.after)
    page = client.get(response["Location"])
    assert payment.provider_ref not in page.content.decode()
    assert shown("No stray-payment alert is open.", page.content.decode())
    # The nightly job, 20 days on, reminds no more about it.
    stray.sweep_stray_captures(now=timezone.now() + dt.timedelta(days=20))
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_REMINDED).exists()


def test_a_second_close_is_a_no_op_with_a_message() -> None:
    payment = flag()
    client, _support = staff("owner")
    client.post(PAGE, {"payment": payment.provider_ref, "reason": REASON})
    again = client.post(PAGE, {"payment": payment.provider_ref, "reason": REASON}, follow=True)
    assert again.status_code == 200
    assert shown("This alert was already closed.", again.content.decode())
    assert AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).count() == 1


def test_a_close_needs_a_reason() -> None:
    payment = flag()
    client, _support = staff("support")
    response = client.post(PAGE, {"payment": payment.provider_ref, "reason": "   "})
    assert response.status_code == 400
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()


def test_a_close_needs_the_csrf_token() -> None:
    payment = flag()
    support = UserFactory.create(platform_role="support")
    client = Client(enforce_csrf_checks=True)
    verified_login(client, support)
    response = client.post(PAGE, {"payment": payment.provider_ref, "reason": REASON})
    assert response.status_code == 403
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).exists()


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The fake provider's refund reader, counting its calls: `refunded[ref]` is its answer."""
    state: dict[str, Any] = {"calls": 0, "refunded": {}}

    def reader(*, provider_ref: str, **_kw: Any) -> Decimal:
        state["calls"] += 1
        return Decimal(state["refunded"].get(provider_ref, "0"))

    monkeypatch.setattr(registry.get_provider("fake"), "refunded_amount", reader)
    return state


def test_the_list_makes_no_provider_call(provider: dict[str, Any]) -> None:
    for _ in range(3):
        flag()
    client, _support = staff("support")
    page = client.get(PAGE)
    assert page.status_code == 200
    assert provider["calls"] == 0
    assert shown("Not checked yet", page.content.decode())
    assert all(row.checked_at is None for row in stray.open_alerts())


def test_check_now_calls_the_provider_once_and_records_it(provider: dict[str, Any]) -> None:
    full, other = flag(amount="10.00"), flag(amount="10.00")
    provider["refunded"][full.provider_ref] = "10.00"
    client, support = staff("support")
    response = client.post(PAGE, {"action": "check", "payment": full.provider_ref})
    assert response.status_code == 302
    assert provider["calls"] == 1
    entry = AuditLog.objects.get(action=PAYMENT_STRAY_CHECKED, target_key=full.provider_ref)
    assert entry.actor_id == support.pk
    assert (entry.after or {})["refunded"] == "10.00"
    rows = {row.payment_ref: row for row in stray.open_alerts()}
    assert rows[full.provider_ref].refund_confirmed is True
    assert rows[full.provider_ref].checked_at is not None
    assert rows[other.provider_ref].checked_at is None
    client.get(PAGE)
    assert provider["calls"] == 1  # the list reads the recorded check, not the provider


def test_check_now_by_finance_answers_403_and_needs_csrf(provider: dict[str, Any]) -> None:
    payment = flag()
    client, _finance = staff("finance")
    assert client.post(PAGE, {"action": "check", "payment": payment.provider_ref}).status_code == (
        403
    )
    support = UserFactory.create(platform_role="support")
    strict = Client(enforce_csrf_checks=True)
    verified_login(strict, support)
    assert strict.post(PAGE, {"action": "check", "payment": payment.provider_ref}).status_code == (
        403
    )
    assert provider["calls"] == 0
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CHECKED).exists()


def test_the_nightly_reminder_records_the_refund_check(provider: dict[str, Any]) -> None:
    payment = flag(amount="10.00")
    provider["refunded"][payment.provider_ref] = "1.00"
    night = timezone.now() + dt.timedelta(days=20)
    stray.sweep_stray_captures(now=night)
    assert provider["calls"] == 1
    (row,) = stray.open_alerts(now=night)
    assert row.refund_confirmed is False
    assert row.checked_at == night
    assert row.age_days == 20
    assert provider["calls"] == 1


def test_a_close_makes_no_provider_call(provider: dict[str, Any]) -> None:
    payment = flag()
    client, _support = staff("support")
    client.post(PAGE, {"payment": payment.provider_ref, "reason": REASON})
    assert AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).count() == 1
    assert provider["calls"] == 0


def test_the_list_query_count_does_not_grow_with_rows() -> None:
    flag()
    client, _support = staff("support")
    client.get(PAGE)  # warm caches
    with CaptureQueriesContext(connection) as few:
        assert client.get(PAGE).status_code == 200
    for _ in range(8):
        flag()
    with CaptureQueriesContext(connection) as many:
        client.get(PAGE)
    assert len(many) == len(few)


def test_check_now_is_rate_limited_per_user(provider: dict[str, Any]) -> None:
    from apps.manager.stray_views import CHECK_LIMIT

    payment = flag()
    client, _support = staff("support")
    check = {"action": "check", "payment": payment.provider_ref}
    for _ in range(CHECK_LIMIT):
        assert client.post(PAGE, check).status_code == 302
    refused = client.post(PAGE, check)
    assert refused.status_code == 429
    assert int(refused["Retry-After"]) >= 1
    assert provider["calls"] == CHECK_LIMIT  # the refused check never reaches the provider
    assert AuditLog.objects.filter(action=PAYMENT_STRAY_CHECKED).count() == CHECK_LIMIT
    other, _owner = staff("owner")  # the limit is per user
    assert other.post(PAGE, check).status_code == 302
    assert provider["calls"] == CHECK_LIMIT + 1


class _CheckButtons(HTMLParser):
    """The text (accessible name) of each submit button in a `action=check` form."""

    def __init__(self) -> None:
        super().__init__()
        self.names: list[str] = []
        self._in_check_form = False
        self._button: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "form":
            self._in_check_form = False
        elif tag == "input" and found.get("name") == "action" and found.get("value") == "check":
            self._in_check_form = True
        elif tag == "button" and self._in_check_form:
            self._button = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_check_form = False
        elif tag == "button" and self._button is not None:
            self.names.append(" ".join("".join(self._button).split()))
            self._button = None

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button.append(data)


def test_each_check_now_button_has_a_unique_accessible_name() -> None:
    payments = [flag() for _ in range(3)]
    client, _support = staff("support")
    page = client.get(PAGE)
    parser = _CheckButtons()
    parser.feed(page.content.decode())
    assert len(parser.names) == len(payments)
    assert len(set(parser.names)) == len(payments)
    with override(settings.LANGUAGE_CODE):
        visible = gettext("Check now")
    for payment in payments:
        (name,) = [n for n in parser.names if payment.provider_ref in n]
        assert name.startswith(visible)


# --- a "Check now" racing a close: both lock the payment row ------------------------------------


def _locks_on_payment(queries: CaptureQueriesContext) -> list[str]:
    return [
        q["sql"]
        for q in queries.captured_queries
        if "FOR UPDATE" in q["sql"] and 'FROM "payments_payment"' in q["sql"]
    ]


def test_a_check_after_a_close_is_a_no_op_with_a_message(provider: dict[str, Any]) -> None:
    payment = flag()
    client, _support = staff("support")
    closed = client.post(PAGE, {"payment": payment.provider_ref, "reason": REASON})
    assert closed.status_code == 302
    check = client.post(PAGE, {"action": "check", "payment": payment.provider_ref}, follow=True)
    assert check.status_code == 200
    assert shown("This alert was already closed.", check.content.decode())
    assert provider["calls"] == 0
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CHECKED).exists()


def test_a_close_during_the_provider_call_leaves_the_check_unrecorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment = flag()
    support = UserFactory.create(platform_role="support")

    def reader(**_kw: Any) -> Decimal:  # support closes the alert while the provider answers
        stray.close_stray_capture(payment.provider_ref, actor=support, reason=REASON, ctx=CTX)
        return Decimal("12.50")

    monkeypatch.setattr(registry.get_provider("fake"), "refunded_amount", reader)
    changes = stray.check_refund_now(payment.provider_ref, actor=support, ctx=CTX)
    assert changes.is_empty  # re-read under the lock: the alert is closed, nothing written
    assert not AuditLog.objects.filter(action=PAYMENT_STRAY_CHECKED).exists()
    assert AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).count() == 1


def test_check_now_and_the_close_lock_the_payment_row(provider: dict[str, Any]) -> None:
    payment = flag()
    support = UserFactory.create(platform_role="support")
    with CaptureQueriesContext(connection) as queries:
        stray.check_refund_now(payment.provider_ref, actor=support, ctx=CTX)
    assert _locks_on_payment(queries)
    with CaptureQueriesContext(connection) as queries:
        stray.close_stray_capture(payment.provider_ref, actor=support, reason=REASON, ctx=CTX)
    assert _locks_on_payment(queries)


def test_the_nightly_auto_close_locks_the_payment_row(provider: dict[str, Any]) -> None:
    payment = flag(amount="10.00")
    provider["refunded"][payment.provider_ref] = "10.00"
    with CaptureQueriesContext(connection) as queries:
        stray.sweep_stray_captures(now=timezone.now() + dt.timedelta(days=20))
    assert _locks_on_payment(queries)
    assert AuditLog.objects.filter(action=PAYMENT_STRAY_CLOSED).count() == 1
