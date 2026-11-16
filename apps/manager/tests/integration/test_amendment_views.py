"""ORD.20 part 3: the order-amendment pages of the Manager and the customer area.

Shop space: draft, preview, revise, discard and apply with the passkey prompt (the grant bound to
the amendment). Platform space: the staff tools (carrier, cancel with a full refund) with the
mandatory note and the passkey, the order lookup and the settings page. Customer area: the
version timeline and the cancel-the-rest offer. Tenancy: another shop's package or draft is a
404. Seed (spec 07, ORD.20): A 2 x 50.00 and B 1 x 30.00 net at 22 %, shipping 10.00, 170.80.
Every outcome assertion says in plain words what was expected and what happened.
"""

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts import levels
from apps.accounts.models import User
from apps.accounts.tests.factories import PasskeyDeviceFactory, UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, PlatformOperator
from apps.core.services import configure_platform_operator, current_operator
from apps.ledger.models import Refund
from apps.ledger.tests.integration.test_amendment_refunds import (  # noqa: F401 (fixtures)
    _setup,
    place_seed,
    stripe,
)
from apps.orders import amendment_engine as am
from apps.orders import amendments
from apps.orders.models import AmendmentState, OrderAmendment, VendorOrder
from apps.orders.tests.control import plainly
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.shipping.models import ShippingMethod
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    WeightBandFactory,
)
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

D = Decimal
SHOP = "/manager/shop/orders/"
PLATFORM = "/manager/platform/orders/"


# --- helpers -------------------------------------------------------------------------------------


def shop_login(client: Client, user: User) -> None:
    """A real login of a shop member: no passkey window, no `elevate`, one passkey enrolled."""
    PasskeyDeviceFactory.create(user=user)
    verified_login(client, user, level=levels.NO_WINDOW)


def staff_login(client: Client, role: str = "support") -> User:
    user = UserFactory.create(platform_role=role)
    PasskeyDeviceFactory.create(user=user)
    verified_login(client, user, level=levels.NO_WINDOW)
    return user


def member(sub: VendorOrder, role: str = "owner") -> User:
    return VendorMemberFactory.create(vendor=sub.vendor, role=role).user


def line_key(sub: VendorOrder, title: str) -> str:
    return str(sub.lines.get(title=title).public_id)


def amend_url(sub: VendorOrder) -> str:
    return f"{SHOP}{sub.public_id}/amend/"


def remove_b(client: Client, sub: VendorOrder, *, unavailable: bool = True) -> Any:
    key = line_key(sub, "B")
    data = {f"line_{key}_remove": "on", "reason": "B is out of stock at the supplier"}
    if unavailable:
        data[f"line_{key}_unavailable"] = "on"
    return client.post(amend_url(sub), data)


def draft_of(sub: VendorOrder) -> OrderAmendment:
    found = amendments.open_amendment(sub)
    assert found is not None, plainly("a draft must exist after the form was sent", "none")
    return found


def grant(client: Client, user: User, amendment_or_ask: Any) -> None:
    """What the passkey prompt's "Do it once" ceremony mints in this browser session."""
    ask = (
        amendments.amendment_passkey(amendment_or_ask)
        if isinstance(amendment_or_ask, OrderAmendment)
        else amendment_or_ask
    )
    assert ask is not None
    key = client.session.session_key
    assert key
    levels.create_grant(key, user, ask.action, ask.digest, ttl=120)


def cheaper_method(sub: VendorOrder) -> None:
    current = ShippingMethod.objects.get(vendor=sub.vendor, key="standard")
    found = ShippingMethodFactory.create(
        vendor=sub.vendor,
        zone=current.zone,
        key="economy",
        min_delivery_days=1,
        max_delivery_days=5,
    )
    ShippingMethodTranslationFactory.create(method=found, name="Economy", status="published")
    WeightBandFactory.create(method=found, from_kg=D("0"), to_kg=D("30"), cost_net=D("8.00"))


@pytest.fixture
def seeded(stripe: FakeStripeClient) -> VendorOrder:  # noqa: F811
    customer = UserFactory.create(email="buyer@example.com")
    return place_seed(stripe, customer=customer)


# --- the shop space ------------------------------------------------------------------------------


def test_the_owner_drafts_a_removal_and_sees_the_preview(
    client: Client, seeded: VendorOrder
) -> None:
    owner = member(seeded)
    shop_login(client, owner)
    page = client.get(amend_url(seeded))
    assert page.status_code == 200
    assert "Prepare a change" in page.text
    response = remove_b(client, seeded)
    assert response.status_code == 302, plainly(
        "a valid draft must redirect to the preview", f"it answered {response.status_code}"
    )
    amendment = draft_of(seeded)
    assert amendment.kind == "minor"
    assert amendment.preview["refund"] == "36.60"
    shown = client.get(amend_url(seeded)).text
    assert "36.60 EUR refunded to the customer" in shown, plainly(
        "the preview must say 36.60 EUR is refunded", "it does not"
    )
    assert "Minor change: it applies at once" in shown
    assert "asks for your passkey" in shown


def test_the_unavailable_box_is_explicit_and_kept_in_the_draft(
    client: Client, seeded: VendorOrder
) -> None:
    shop_login(client, member(seeded))
    remove_b(client, seeded, unavailable=False)
    change = draft_of(seeded).changes[0]
    assert all((change["kind"] == "remove_line", change["unavailable"] is False)), plainly(
        "an unticked 'not available' box must store a removal that restocks", f"stored {change}"
    )


def test_applying_asks_the_passkey_then_applies_v2_and_refunds(
    client: Client, seeded: VendorOrder
) -> None:
    owner = member(seeded)
    shop_login(client, owner)
    remove_b(client, seeded)
    amendment = draft_of(seeded)
    apply_url = f"{amend_url(seeded)}{amendment.public_id}/apply/"
    prompt = client.post(apply_url)
    assert prompt.status_code == 403, plainly(
        "applying a refund without a fresh passkey must show the passkey prompt",
        f"it answered {prompt.status_code}",
    )
    assert 'data-passkey="once"' in prompt.text
    ask = amendments.amendment_passkey(amendment)
    assert ask is not None
    assert ask.digest in prompt.text
    assert "no-store" in prompt["Cache-Control"] or "no-cache" in prompt["Cache-Control"], plainly(
        "the passkey prompt must never be cached", f"Cache-Control is {prompt['Cache-Control']}"
    )
    offers = client.session.get(levels.SHOP_OFFERS_KEY, [])
    assert any(o["digest"] == ask.digest for o in offers), plainly(
        "the prompt must offer this owner the confirmation of this amendment", f"offers {offers}"
    )
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.DRAFT

    grant(client, owner, amendment)
    done = client.post(apply_url)
    assert done.status_code == 302, plainly(
        "with the grant the change must apply", f"it answered {done.status_code}"
    )
    amendment.refresh_from_db()
    seeded.refresh_from_db()
    assert amendment.state == AmendmentState.APPLIED
    assert seeded.current_version is not None
    assert seeded.current_version.number == 2
    refund = Refund.objects.get(amendment=amendment)
    assert refund.refunded_gross == D("36.60"), plainly(
        "removing B must refund 36.60", f"it refunded {refund.refunded_gross}"
    )
    message = client.get(amend_url(seeded)).text
    assert "36.60 EUR is refunded to the customer" in message
    assert "within 14 days" in message


def test_the_passkey_endpoint_accepts_the_offer_to_a_member_with_orders(
    client: Client, seeded: VendorOrder
) -> None:
    clerk = member(seeded, role="staff")
    shop_login(client, clerk)
    remove_b(client, seeded)
    amendment = draft_of(seeded)
    assert amendment.initiator == "vendor_member"
    client.post(f"{amend_url(seeded)}{amendment.public_id}/apply/")
    ask = amendments.amendment_passkey(amendment)
    assert ask is not None
    assert ask.action == "vendor_refund"
    options = client.post(
        "/account/level/once/options/",
        json.dumps({"action": ask.action, "digest": ask.digest}),
        content_type="application/json",
    )
    assert options.status_code == 200, plainly(
        "a member with orders must get the passkey options for the offered amendment",
        f"it answered {options.status_code}",
    )
    other = client.post(
        "/account/level/once/options/",
        json.dumps({"action": "payout_details", "digest": ask.digest}),
        content_type="application/json",
    )
    assert other.status_code == 403, plainly(
        "a member must not confirm an owner action through an amendment offer",
        f"it answered {other.status_code}",
    )


def test_may_confirm_follows_the_orders_right(seeded: VendorOrder, settings: Any) -> None:
    clerk = member(seeded, role="staff")
    PasskeyDeviceFactory.create(user=clerk)
    assert levels.may_confirm(clerk, "vendor_refund", seeded.vendor)
    assert levels.may_confirm(clerk, "order_amend_address", seeded.vendor)
    assert not levels.may_confirm(clerk, "payout_details", seeded.vendor)
    assert not levels.may_confirm(clerk, "order_amend_cancel", seeded.vendor)
    settings.SHOP_ORDERS_CHECK = f"{__name__}.never"
    assert not levels.may_confirm(clerk, "vendor_refund", seeded.vendor), plainly(
        "a member without the orders right must not confirm an amendment refund", "it may"
    )


def never(user: Any, shop: Any) -> bool:
    return False


def test_a_member_without_the_right_sees_a_plain_refusal_on_the_prompt(
    client: Client, seeded: VendorOrder, settings: Any
) -> None:
    clerk = member(seeded, role="staff")
    shop_login(client, clerk)
    remove_b(client, seeded)
    amendment = draft_of(seeded)
    settings.SHOP_ORDERS_CHECK = f"{__name__}.never"
    prompt = client.post(f"{amend_url(seeded)}{amendment.public_id}/apply/")
    assert prompt.status_code == 403
    assert 'data-passkey="once"' not in prompt.text
    assert "Your role may not confirm this change" in prompt.text


def test_a_revision_voids_the_grant_and_a_discard_closes_the_draft(
    client: Client, seeded: VendorOrder
) -> None:
    owner = member(seeded)
    shop_login(client, owner)
    remove_b(client, seeded)
    amendment = draft_of(seeded)
    grant(client, owner, amendment)
    key = line_key(seeded, "A")
    client.post(
        amend_url(seeded),
        {f"line_{key}_price": "45.00", "reason": "Price was wrong"},
    )
    amendment.refresh_from_db()
    assert all((amendment.revision == 2, amendment.preview["refund"] == "12.20")), plainly(
        "the revision must recompute 12.20 for A at 45.00",
        f"it shows {amendment.preview['refund']}",
    )
    stale = client.post(f"{amend_url(seeded)}{amendment.public_id}/apply/")
    assert stale.status_code == 403, plainly(
        "a grant made before the edit must not apply the edited draft",
        f"it answered {stale.status_code}",
    )
    gone = client.post(f"{amend_url(seeded)}{amendment.public_id}/discard/")
    assert gone.status_code == 302
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.DISCARDED


def test_refusals_are_plain(client: Client, seeded: VendorOrder) -> None:
    owner = member(seeded)
    shop_login(client, owner)
    empty = client.post(amend_url(seeded), {"reason": "nothing"})
    assert empty.status_code == 400
    assert "Choose at least one change" in empty.text
    no_reason = client.post(amend_url(seeded), {f"line_{line_key(seeded, 'B')}_remove": "on"})
    assert no_reason.status_code == 400
    client.post(f"{SHOP}{seeded.public_id}/accept/")
    client.post(f"{SHOP}{seeded.public_id}/ship/", {"tracking_number": "TRK1"})
    seeded.refresh_from_db()
    assert seeded.state == "shipped"
    shipped = remove_b(client, seeded)
    assert shipped.status_code == 409, plainly(
        "a shipped package must refuse a change", f"it answered {shipped.status_code}"
    )
    assert "can be amended only before shipping" in shipped.text


def test_another_shop_s_package_and_draft_are_404(
    client: Client,
    seeded: VendorOrder,
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    theirs = place_seed(stripe)
    shop_login(client, member(seeded))
    assert client.get(amend_url(theirs)).status_code == 404
    assert remove_b(client, theirs).status_code == 404
    their_owner = member(theirs)
    amendment, _ = amendments.draft_amendment(
        theirs,
        [am.RemoveLine(line_key(theirs, "B"))],
        actor=their_owner,
        reason="Out of stock",
        ctx=AuditContext(actor_label="t", source=AuditLog.Source.VENDOR),
    )
    assert amendment is not None
    for path in (
        f"{amend_url(theirs)}{amendment.public_id}/apply/",
        f"{amend_url(seeded)}{amendment.public_id}/apply/",
        f"{amend_url(seeded)}{amendment.public_id}/discard/",
    ):
        assert client.post(path).status_code == 404, plainly(
            f"another shop's draft must be a 404 at {path}", "it was reachable"
        )


def test_the_order_page_links_the_change_and_explains_the_hold(
    client: Client, seeded: VendorOrder
) -> None:
    owner = member(seeded)
    shop_login(client, owner)
    page = client.get(f"{SHOP}{seeded.public_id}/")
    assert "Change this order" in page.text
    remove_b(client, seeded)
    amendment = draft_of(seeded)
    grant(client, owner, amendment)
    client.post(f"{amend_url(seeded)}{amendment.public_id}/apply/")
    client.post(f"{SHOP}{seeded.public_id}/accept/")
    refused = client.post(
        f"{SHOP}{seeded.public_id}/ship/", {"tracking_number": "TRK1", "carrier": "Poste"}
    )
    assert refused.status_code == 409
    assert "cannot be shipped before" in refused.text, plainly(
        "shipping during the cancel-the-rest hold must be refused in plain words", "it was not"
    )


# --- the platform space --------------------------------------------------------------------------


def test_support_finds_the_order_and_changes_the_carrier_with_note_and_passkey(
    client: Client, seeded: VendorOrder
) -> None:
    cheaper_method(seeded)
    support = staff_login(client)
    found = client.get(f"{PLATFORM}?number={seeded.order.number}")
    assert found.status_code == 200
    assert f"{PLATFORM}{seeded.public_id}/" in found.text
    page = f"{PLATFORM}{seeded.public_id}/"
    assert client.get(page).status_code == 200
    no_note = client.post(page, {"carrier": "economy", "reason": "Customer asked"})
    assert no_note.status_code == 400, plainly(
        "a staff change without a note must be refused", f"it answered {no_note.status_code}"
    )
    client.post(page, {"carrier": "economy", "reason": "Customer asked", "staff_note": "call"})
    amendment = draft_of(seeded)
    assert amendment.initiator == "support"
    assert amendment.preview["refund"] == "2.44"
    apply_url = f"{page}amendments/{amendment.public_id}/apply/"
    prompt = client.post(apply_url)
    assert prompt.status_code == 403
    assert "order_amend_cancel" in prompt.text
    grant(client, support, amendment)
    assert client.post(apply_url).status_code == 302
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.APPLIED


def test_support_has_no_line_fields_and_cannot_touch_a_shop_draft(
    client: Client, seeded: VendorOrder
) -> None:
    owner = member(seeded)
    amendments.draft_amendment(
        seeded,
        [am.RemoveLine(line_key(seeded, "B"))],
        actor=owner,
        reason="Out of stock",
        ctx=AuditContext(actor_label="t", source=AuditLog.Source.VENDOR),
    )
    staff_login(client)
    page = client.get(f"{PLATFORM}{seeded.public_id}/")
    assert "line_" not in page.text, plainly("staff must get no line fields", "they did")
    assert "started by the shop" in page.text
    amendment = draft_of(seeded)
    refused = client.post(f"{PLATFORM}{seeded.public_id}/amendments/{amendment.public_id}/discard/")
    assert refused.status_code == 409


def test_platform_pages_refuse_vendors_and_other_roles(client: Client, seeded: VendorOrder) -> None:
    shop_login(client, member(seeded))
    assert client.get(PLATFORM).status_code in {403, 404}
    finance = Client()
    staff_login(finance, "finance")
    assert finance.get(PLATFORM).status_code == 404
    assert finance.get(f"{PLATFORM}{seeded.public_id}/").status_code == 404


def test_the_settings_page_shows_the_live_configuration(client: Client) -> None:
    staff_login(client, "config")
    page = client.get("/manager/platform/settings/order-changes/")
    assert page.status_code == 200
    for text in ("72 hours", "2 days", "24 hours", "the order stays as it was"):
        assert text in page.text, plainly(f"the settings page must show {text!r}", "it does not")
    latest = current_operator()
    new = PlatformOperator(
        **{
            f.name: getattr(latest, f.name)
            for f in PlatformOperator._meta.concrete_fields
            if f.name not in {"id", "public_id", "created_at", "updated_at", "valid_to", "logo"}
        }
    )
    new.valid_from = timezone.now() - dt.timedelta(seconds=1)
    new.amendment_answer_hours = 48
    configure_platform_operator(
        new, ctx=AuditContext(actor_label="t", source=AuditLog.Source.ADMIN)
    )
    assert amendments.amendment_settings().answer_hours == 48
    assert "48 hours" in client.get("/manager/platform/settings/order-changes/").text, plainly(
        "the page must read the configuration in force", "it shows the old value"
    )
    support = Client()
    staff_login(support)
    assert support.get("/manager/platform/settings/order-changes/").status_code == 404


# --- the customer area ---------------------------------------------------------------------------


def applied_removal(seeded: VendorOrder) -> OrderAmendment:
    owner = member(seeded)
    ctx = AuditContext(actor_label="t", source=AuditLog.Source.VENDOR)
    amendment, _ = amendments.draft_amendment(
        seeded,
        [am.RemoveLine(line_key(seeded, "B"))],
        actor=owner,
        reason="Gone",
        ctx=ctx,
    )
    assert amendment is not None
    ask = amendments.amendment_passkey(amendment)
    assert ask is not None
    levels.create_grant("s" * 32, owner, ask.action, ask.digest, ttl=120)
    amendments.apply_amendment(amendment, actor=owner, ctx=ctx, passkey_session="s" * 32)
    return amendment


def test_the_customer_sees_v2_the_refund_and_the_offer(client: Client, seeded: VendorOrder) -> None:
    applied_removal(seeded)
    customer = seeded.order.customer
    assert customer is not None
    client.force_login(customer)
    page = client.get(f"/account/orders/{seeded.order.public_id}/").text
    for text in ("Version 2", "B removed as unavailable", "36.60 EUR refunded", "134.20 EUR"):
        assert text in page, plainly(f"the customer's order page must show {text!r}", "it does not")
    assert "Cancel the rest of the order and refund everything" in page
    assert "Staff note" not in page


def test_the_customer_keeps_or_cancels_the_rest(client: Client, seeded: VendorOrder) -> None:
    applied_removal(seeded)
    customer = seeded.order.customer
    assert customer is not None
    client.force_login(customer)
    base = f"/account/orders/{seeded.order.public_id}/packages/{seeded.public_id}/"
    assert client.get(f"{base}cancel-rest/").status_code == 405
    assert client.post(f"{base}cancel-rest/").status_code == 302
    seeded.refresh_from_db()
    assert seeded.state == "cancelled", plainly(
        "cancelling the rest must cancel the package", f"it is {seeded.state}"
    )
    page = client.get(f"/account/orders/{seeded.order.public_id}/").text
    assert "Cancel the rest of the order" not in page


def test_keeping_the_rest_ends_the_hold_and_the_offer_stays(
    client: Client, seeded: VendorOrder
) -> None:
    applied_removal(seeded)
    customer = seeded.order.customer
    assert customer is not None
    client.force_login(customer)
    base = f"/account/orders/{seeded.order.public_id}/packages/{seeded.public_id}/"
    assert client.post(f"{base}keep-rest/").status_code == 302
    seeded.refresh_from_db()
    assert seeded.ship_hold_until is None
    assert seeded.cancel_rest_offered_at is not None, plainly(
        "keeping the rest only ends the hold: the offer stays open until shipping",
        "the offer was closed",
    )
    page = client.get(f"/account/orders/{seeded.order.public_id}/").text
    assert "Keep the rest" not in page
    assert "will not ship it before" not in page, plainly(
        "a hold that ended must not be shown as a date", "it was shown"
    )
    assert "Cancel the rest of the order and refund everything" in page
    assert client.post(f"{base}cancel-rest/").status_code == 302
    seeded.refresh_from_db()
    assert seeded.state == "cancelled", plainly(
        "the rest can still be cancelled after keeping it, until shipping", f"it is {seeded.state}"
    )
    page = client.get(f"/account/orders/{seeded.order.public_id}/").text
    assert "Rest of the package cancelled at your request" in page, plainly(
        "the cancellation must appear in the customer's timeline", "it does not"
    )


def test_a_stranger_cannot_cancel_the_rest(client: Client, seeded: VendorOrder) -> None:
    applied_removal(seeded)
    client.force_login(UserFactory.create())
    base = f"/account/orders/{seeded.order.public_id}/packages/{seeded.public_id}/"
    assert client.post(f"{base}cancel-rest/").status_code == 404
    assert client.post(f"{base}keep-rest/").status_code == 404
    seeded.refresh_from_db()
    assert seeded.cancel_rest_offered_at is not None
