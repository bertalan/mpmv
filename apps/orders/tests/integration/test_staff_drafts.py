"""ORD.32: a staff draft from a past order, with a one-time payment link.

Done-when: a draft from a past order holds today's prices and skips unavailable lines with the
reason; staff cannot change a price (service test); the link opens the draft for that customer
only, a second use or an expired link is refused, and a GET changes nothing; the link cannot be
sent to any other address and a fourth send in a day is refused; paying through the link places
one normal order with the usual snapshots, and an unpaid draft creates no order, no stock
reservation and no ledger line; a Catalog or Finance role member cannot create a draft; every
step writes an `AuditLog` entry with role and note.

Also: the link is POST-confirmed, carries no personal data, expires after the operator setting
(default 7 days) and stops working when the draft changes; quantities stay within the stock;
the email renders in the five languages, informal in Italian; the token never reaches the logs.
Needs human review: orders, payments, consumer law.
"""

import datetime as dt
import inspect
import re
import secrets
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.factories import PasskeyDeviceFactory, UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cart.models import Cart
from apps.cart.session import GUEST_CART_SESSION_KEY
from apps.catalog.models import Product
from apps.core import services as core_services
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, OutboxEmail
from apps.core.tests.factories import PlatformOperatorFactory, utc
from apps.orders import engine, staff_drafts
from apps.orders.models import Order, StaffDraft, StaffDraftLine, StaffDraftState, StockReservation
from apps.orders.tests.integration.test_order_again import order_of
from apps.orders.tests.integration.test_order_again import product as make_product
from apps.orders.tests.integration.test_order_again import vendor as make_vendor
from mpmv.log_redaction import REDACTED, redact_secret_paths

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:test", source=AuditLog.Source.ADMIN)
NOTE = "The customer asked by phone to order the same again."


def staff(role: str = "support") -> User:
    return UserFactory.create(platform_role=role)


def manager_client(role: str = "support") -> tuple[Client, User]:
    user = staff(role)
    PasskeyDeviceFactory.create(user=user)
    client = Client()
    verified_login(client, user, level=levels.NO_WINDOW)
    return client, user


def customer() -> tuple[Client, User]:
    user = UserFactory.create(password=secrets.token_urlsafe(16))
    client = Client()
    client.force_login(user)
    return client, user


def draft_of(order: Order, actor: User | None = None) -> StaffDraft:
    result = staff_drafts.draft_from_order(order, actor=actor or staff(), note=NOTE, ctx=CTX)
    assert result.draft is not None
    return result.draft


def sent_token(draft: StaffDraft, actor: User | None = None) -> str:
    staff_drafts.send_link(draft, actor=actor or staff(), note=NOTE, ctx=CTX)
    mail = OutboxEmail.objects.filter(template_key=staff_drafts.LINK_EMAIL).latest("id")
    match = re.search(r"/account/cart-draft/([^/]+)/", str(mail.context_data["link"]))
    assert match is not None
    return match.group(1)


def open_with(client: Client, token: str) -> Any:
    return client.post(reverse("staff_draft_link_continue"), {"token": token})


def cart_quantities(user: User) -> dict[int, int]:
    cart = Cart.objects.get(user=user)
    return {line.product_id: line.quantity for line in cart.lines.all()}


def actions(name: str) -> list[AuditLog]:
    return list(AuditLog.objects.filter(action=name).order_by("id"))


# --- the draft -------------------------------------------------------------------------------


def test_a_draft_holds_todays_prices_and_skips_unavailable_lines_with_the_reason() -> None:
    _, user = customer()
    v = make_vendor()
    a, b, retired, empty = (
        make_product(v, price="10.00"),
        make_product(v),
        make_product(v),
        make_product(v, stock=0),
    )
    order = order_of(user, [(a, 2), (b, 1), (retired, 1), (empty, 1)])
    retired.status = Product.Status.DRAFT
    retired.save()
    a.price_net = Decimal("12.50")
    a.save()
    result = staff_drafts.draft_from_order(order, actor=staff(), note=NOTE, ctx=CTX)
    draft = result.draft
    assert draft is not None
    assert draft.customer == user
    assert {(line.product_id, line.quantity) for line in draft.lines.all()} == {
        (a.pk, 2),
        (b.pk, 1),
    }
    assert sorted(item["reason"] for item in draft.skipped) == [
        engine.REORDER_OUT_OF_STOCK,
        engine.REORDER_RETIRED,
    ]
    client, _ = manager_client()
    page = client.get(reverse("manager:platform:order_draft", args=[draft.public_id]))
    assert page.status_code == 200
    assert "12.50 EUR" in page.text  # today's price, never the 1.00 of the order
    assert "1.00 EUR" not in page.text
    assert "not sold any more" in page.text
    assert "out of stock" in page.text


def test_a_quantity_above_todays_stock_is_capped_when_drafting() -> None:
    _, user = customer()
    p = make_product(make_vendor(), stock=2)
    draft = draft_of(order_of(user, [(p, 5)]))
    assert draft.lines.get().quantity == 2


def test_a_guest_order_makes_a_guest_draft_and_one_open_draft_per_order() -> None:
    p = make_product(make_vendor())
    order = order_of(None, [(p, 1)])
    first = draft_of(order)
    assert first.customer is None
    again = staff_drafts.draft_from_order(order, actor=staff(), note=NOTE, ctx=CTX)
    assert again.existing
    assert again.draft == first
    assert StaffDraft.objects.count() == 1


def test_a_draft_needs_a_note_and_a_dry_run_writes_nothing() -> None:
    order = order_of(None, [(make_product(make_vendor()), 1)])
    with pytest.raises(staff_drafts.DraftRefused):
        staff_drafts.draft_from_order(order, actor=staff(), note="  ", ctx=CTX)
    planned = staff_drafts.draft_from_order(order, actor=staff(), note=NOTE, ctx=CTX, dry_run=True)
    assert not planned.changeset.is_empty
    assert StaffDraft.objects.count() == 0
    assert AuditLog.objects.filter(action__startswith="orders.staff_draft").count() == 0


# --- quantities, never prices ----------------------------------------------------------------


def test_staff_cannot_change_a_price() -> None:
    """The service takes quantities only; a draft line has no price to change."""
    p = make_product(make_vendor(), price="10.00")
    draft = draft_of(order_of(None, [(p, 1)]))
    line = draft.lines.get()
    assert "price" not in inspect.signature(staff_drafts.set_quantities).parameters
    with pytest.raises(TypeError):
        staff_drafts.set_quantities(  # type: ignore[call-arg]
            draft, {line.public_id: 1}, actor=staff(), note=NOTE, ctx=CTX, price=Decimal("1.00")
        )
    assert not any("price" in f.name for f in StaffDraftLine._meta.get_fields())
    client, _ = manager_client()
    response = client.post(
        reverse("manager:platform:order_draft_lines", args=[draft.public_id]),
        {f"qty-{line.public_id}": "1", f"price-{line.public_id}": "0.01", "lines-note": NOTE},
    )
    assert response.status_code == 302
    token = sent_token(draft)
    guest = Client()
    open_with(guest, token)
    cart = Cart.objects.get(session_key=guest.session[GUEST_CART_SESSION_KEY])
    assert cart.lines.get().added_price_net == Decimal("10.00")


def test_quantities_change_up_to_the_stock_and_zero_removes_a_line() -> None:
    v = make_vendor()
    a, b = make_product(v, stock=3), make_product(v)
    draft = draft_of(order_of(None, [(a, 1), (b, 1)]))
    line_a, line_b = draft.lines.order_by("id")
    with pytest.raises(staff_drafts.DraftRefused, match="only 3 available"):
        staff_drafts.set_quantities(draft, {line_a.public_id: 4}, actor=staff(), note=NOTE, ctx=CTX)
    staff_drafts.set_quantities(
        draft, {line_a.public_id: 3, line_b.public_id: 0}, actor=staff(), note=NOTE, ctx=CTX
    )
    assert list(draft.lines.values_list("quantity", flat=True)) == [3]
    assert draft.version == 2


def test_a_change_of_the_draft_stops_the_link_already_sent() -> None:
    draft = draft_of(order_of(None, [(make_product(make_vendor()), 1)]))
    token = sent_token(draft)
    line = draft.lines.get()
    staff_drafts.set_quantities(draft, {line.public_id: 2}, actor=staff(), note=NOTE, ctx=CTX)
    assert staff_drafts.check_link(token).refusal == engine.DRAFT_LINK_CHANGED
    response = open_with(Client(), token)
    assert response.status_code == 410
    assert StaffDraft.objects.get(pk=draft.pk).state == StaffDraftState.OPEN


# --- sending ---------------------------------------------------------------------------------


def test_the_link_goes_only_to_the_orders_account_email_whatever_is_posted() -> None:
    _, user = customer()
    draft = draft_of(order_of(user, [(make_product(make_vendor()), 1)]))
    assert "recipient" not in inspect.signature(staff_drafts.send_link).parameters
    assert "email" not in inspect.signature(staff_drafts.send_link).parameters
    client, _ = manager_client()
    response = client.post(
        reverse("manager:platform:order_draft_send", args=[draft.public_id]),
        {"send-note": NOTE, "email": "someone.else@example.com", "recipient": "x@example.com"},
    )
    assert response.status_code == 302
    mail = OutboxEmail.objects.get(template_key=staff_drafts.LINK_EMAIL)
    assert mail.recipient == user.email


def test_a_guest_draft_link_goes_to_the_guest_email_of_the_order() -> None:
    order = order_of(None, [(make_product(make_vendor()), 1)])
    sent_token(draft_of(order))
    assert OutboxEmail.objects.get(template_key=staff_drafts.LINK_EMAIL).recipient == order.email


def test_a_fourth_send_in_a_day_is_refused() -> None:
    draft = draft_of(order_of(None, [(make_product(make_vendor()), 1)]))
    for _ in range(3):
        staff_drafts.send_link(draft, actor=staff(), note=NOTE, ctx=CTX)
    with pytest.raises(staff_drafts.DraftRefused, match="3 times today"):
        staff_drafts.send_link(draft, actor=staff(), note=NOTE, ctx=CTX)
    assert OutboxEmail.objects.filter(template_key=staff_drafts.LINK_EMAIL).count() == 3
    client, _ = manager_client()
    response = client.post(
        reverse("manager:platform:order_draft_send", args=[draft.public_id]), {"send-note": NOTE}
    )
    assert response.status_code == 409


def test_a_new_send_replaces_the_link_sent_before() -> None:
    draft = draft_of(order_of(None, [(make_product(make_vendor()), 1)]))
    first = sent_token(draft)
    second = sent_token(draft)
    assert staff_drafts.check_link(first).refusal == engine.DRAFT_LINK_UNKNOWN
    assert staff_drafts.check_link(second).refusal is None


def test_the_link_carries_no_personal_data_and_only_its_digest_is_stored() -> None:
    _, user = customer()
    order = order_of(user, [(make_product(make_vendor()), 1)])
    draft = draft_of(order)
    token = sent_token(draft)
    draft.refresh_from_db()
    for value in (user.email, str(user.public_id), order.number, str(draft.public_id)):
        assert value not in token
    assert draft.link_digest == engine.draft_token_digest(token) != token
    stored = {str(v) for v in StaffDraft.objects.filter(pk=draft.pk).values().get().values()}
    assert token not in stored


def test_the_link_expires_after_the_operator_setting_default_seven_days() -> None:
    draft = draft_of(order_of(None, [(make_product(make_vendor()), 1)]))
    before = timezone.now()
    sent_token(draft)
    draft.refresh_from_db()
    assert draft.link_expires_at is not None
    assert dt.timedelta(days=7) <= draft.link_expires_at - before < dt.timedelta(days=7, minutes=1)
    PlatformOperatorFactory.create(
        open_ended=True, valid_from=utc(2026, 1, 1), staff_draft_link_days=2
    )
    sent_token(draft)
    draft.refresh_from_db()
    assert draft.link_expires_at - timezone.now() < dt.timedelta(days=2, minutes=1)


# --- the customer opens the link -------------------------------------------------------------


def test_a_get_changes_nothing_and_the_post_opens_the_draft_once() -> None:
    client, user = customer()
    p = make_product(make_vendor())
    draft = draft_of(order_of(user, [(p, 2)]))
    token = sent_token(draft)
    audits = AuditLog.objects.count()
    for _ in range(2):
        page = client.get(reverse("staff_draft_link", args=[token]))
        assert page.status_code == 200
        assert page["Cache-Control"].startswith("max-age=0") or "no-cache" in page["Cache-Control"]
        assert page["Referrer-Policy"] == "strict-origin"
        assert client.head(reverse("staff_draft_link", args=[token])).status_code == 200
    assert AuditLog.objects.count() == audits
    assert not Cart.objects.filter(user=user).exists()
    assert StaffDraft.objects.get(pk=draft.pk).state == StaffDraftState.OPEN
    response = open_with(client, token)
    assert response.status_code == 302
    assert response["Location"].endswith(reverse("orders:cart").split("/", 2)[-1])
    assert cart_quantities(user) == {p.pk: 2}
    assert StaffDraft.objects.get(pk=draft.pk).state == StaffDraftState.OPENED
    second = open_with(client, token)
    assert second.status_code == 410
    assert cart_quantities(user) == {p.pk: 2}  # nothing added twice
    assert [(a.after or {})["reason"] for a in actions(staff_drafts.ACTION_REFUSED)] == [
        engine.DRAFT_LINK_USED
    ]


def test_an_account_draft_opens_for_that_account_only() -> None:
    owner_client, user = customer()
    draft = draft_of(order_of(user, [(make_product(make_vendor()), 1)]))
    token = sent_token(draft)
    stranger, other = customer()
    refused = open_with(stranger, token)
    assert refused.status_code == 403
    assert not Cart.objects.filter(user=other).exists()
    anonymous = open_with(Client(), token)
    assert anonymous.status_code == 302
    assert "/login/" in anonymous["Location"]
    assert StaffDraft.objects.get(pk=draft.pk).state == StaffDraftState.OPEN
    assert open_with(owner_client, token).status_code == 302
    assert StaffDraft.objects.get(pk=draft.pk).state == StaffDraftState.OPENED


def test_a_guest_draft_does_not_open_in_another_customers_account() -> None:
    draft = draft_of(order_of(None, [(make_product(make_vendor()), 1)]))
    token = sent_token(draft)
    stranger, _ = customer()
    assert open_with(stranger, token).status_code == 403
    assert StaffDraft.objects.get(pk=draft.pk).state == StaffDraftState.OPEN


def test_an_expired_link_is_refused() -> None:
    draft = draft_of(order_of(None, [(make_product(make_vendor()), 1)]))
    token = sent_token(draft)
    StaffDraft.objects.filter(pk=draft.pk).update(link_expires_at=timezone.now())
    assert Client().get(reverse("staff_draft_link", args=[token])).status_code == 410
    assert open_with(Client(), token).status_code == 410
    assert StaffDraft.objects.get(pk=draft.pk).state == StaffDraftState.OPEN


def test_a_guessed_token_is_refused_without_writing() -> None:
    audits = AuditLog.objects.count()
    assert open_with(Client(), secrets.token_urlsafe(32)).status_code == 410
    assert AuditLog.objects.count() == audits


def test_an_unpaid_draft_creates_no_order_or_stock_reservation() -> None:
    """The ledger side is checked in apps/ledger/tests/integration/test_staff_draft_checkout.py
    (orders may not import the ledger)."""
    client, user = customer()
    p = make_product(make_vendor(), stock=5)
    order = order_of(user, [(p, 2)])
    orders, reservations = Order.objects.count(), StockReservation.objects.count()
    draft = draft_of(order)
    open_with(client, sent_token(draft))
    assert Order.objects.count() == orders
    assert StockReservation.objects.count() == reservations
    p.refresh_from_db()
    assert p.stock_qty == 5


# --- roles and audit -------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["catalog", "finance", "config"])
def test_a_catalog_finance_or_configuration_member_cannot_create_a_draft(role: str) -> None:
    order = order_of(None, [(make_product(make_vendor()), 1)])
    with pytest.raises(PermissionDenied):
        staff_drafts.draft_from_order(order, actor=staff(role), note=NOTE, ctx=CTX)
    client, _ = manager_client(role)
    response = client.post(
        reverse("manager:platform:order_draft_new", args=[order.public_id]), {"note": NOTE}
    )
    assert response.status_code == 404
    assert client.get(reverse("manager:platform:order_drafts")).status_code == 404
    assert StaffDraft.objects.count() == 0


@pytest.mark.parametrize(("role", "label"), [("support", "support"), ("owner", "platform_owner")])
def test_every_step_writes_an_audit_entry_with_role_and_note(role: str, label: str) -> None:
    client, actor = manager_client(role)
    order = order_of(None, [(make_product(make_vendor()), 2)])
    created = client.post(
        reverse("manager:platform:order_draft_new", args=[order.public_id]), {"note": NOTE}
    )
    assert created.status_code == 302
    draft = StaffDraft.objects.get()
    line = draft.lines.get()
    url = reverse("manager:platform:order_draft_lines", args=[draft.public_id])
    assert client.post(url, {f"qty-{line.public_id}": "1", "lines-note": NOTE}).status_code == 302
    send = reverse("manager:platform:order_draft_send", args=[draft.public_id])
    assert client.post(send, {"send-note": NOTE}).status_code == 302
    for action in (
        staff_drafts.ACTION_CREATED,
        staff_drafts.ACTION_CHANGED,
        staff_drafts.ACTION_SENT,
    ):
        entry = next(e for e in actions(action) if e.target_model == staff_drafts.DRAFT_MODEL)
        assert entry.actor_id == actor.pk
        assert entry.after is not None
        assert entry.after["role"] == label
        assert entry.after["note"] == NOTE
        assert order.email not in str(entry.after)
    mail = OutboxEmail.objects.get(template_key=staff_drafts.LINK_EMAIL)
    token = re.search(r"/account/cart-draft/([^/]+)/", str(mail.context_data["link"])).group(1)  # type: ignore[union-attr]
    open_with(Client(), token)
    opened = actions(staff_drafts.ACTION_OPENED)
    assert len(opened) == 1
    assert opened[0].actor_label == "guest"


def test_a_step_without_a_note_is_refused_on_the_page() -> None:
    client, _ = manager_client()
    order = order_of(None, [(make_product(make_vendor()), 1)])
    client.post(reverse("manager:platform:order_draft_new", args=[order.public_id]), {"note": ""})
    assert StaffDraft.objects.count() == 0
    draft = draft_of(order)
    response = client.post(
        reverse("manager:platform:order_draft_send", args=[draft.public_id]), {"send-note": ""}
    )
    assert response.status_code == 409
    assert not OutboxEmail.objects.filter(template_key=staff_drafts.LINK_EMAIL).exists()


def test_the_platform_order_page_offers_to_prepare_a_cart() -> None:
    client, _ = manager_client()
    order = order_of(None, [(make_product(make_vendor()), 1)])
    sub = order.vendor_orders.get()
    page = client.get(reverse("manager:platform:order", args=[sub.public_id]))
    assert reverse("manager:platform:order_draft_new", args=[order.public_id]) in page.text
    draft = draft_of(order)
    page = client.get(reverse("manager:platform:order", args=[sub.public_id]))
    assert reverse("manager:platform:order_draft", args=[draft.public_id]) in page.text


# --- the email and the logs ------------------------------------------------------------------

LANGS = ("en", "it", "de", "fr", "es")
EMAIL_CONTEXT: dict[str, Any] = {
    "order_number": "MP-1001",
    "customer_name": "Alex",
    "link": "https://example.com/account/cart-draft/x/",
    "expires": "25 November 2026",
    "items": [{"title": "Cup", "variant": "Blue", "quantity": 2}],
}


@pytest.mark.parametrize("lang", LANGS)
def test_the_link_email_renders_in_every_language(lang: str) -> None:
    rendered = core_services.render_email(staff_drafts.LINK_EMAIL, lang, EMAIL_CONTEXT)
    english = core_services.render_email(staff_drafts.LINK_EMAIL, "en", EMAIL_CONTEXT)
    assert "{{" not in rendered.text_body + rendered.html_body
    assert EMAIL_CONTEXT["link"] in rendered.text_body
    assert f'lang="{lang}"' in rendered.html_body
    if lang != "en":
        assert rendered.subject != english.subject
        assert rendered.text_body != english.text_body
        assert "support team" not in rendered.text_body


def test_the_italian_email_speaks_informally() -> None:
    text = core_services.render_email(staff_drafts.LINK_EMAIL, "it", EMAIL_CONTEXT).text_body
    assert "per te" in text
    assert " Lei " not in text
    assert "La preghiamo" not in text


def test_the_token_of_the_link_path_is_redacted_from_logs() -> None:
    token = secrets.token_urlsafe(32)
    line = f'"GET /account/cart-draft/{token}/ HTTP/1.1" 200'
    assert token not in redact_secret_paths(line)
    assert REDACTED in redact_secret_paths(line)
    assert redact_secret_paths("/account/cart-draft/continue/") == "/account/cart-draft/continue/"
