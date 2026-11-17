"""ORD.20 (2/3) in the orders service, without money: the passkey of an address change, staff
refunds, the idle discard, the withdrawal guard, closing an open amendment and draft edits.

The money-moving cases (refunds, ledger rows, the refund fee, the cancel-the-rest refund) need
the ledger and the payment double: they are in `apps/ledger/tests/integration/
test_amendment_refunds.py`. Every assertion says in plain words what was expected and what
happened (owner, 2026-11-16).
"""

import datetime as dt
import secrets
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.accounts import levels
from apps.accounts.tests.factories import UserFactory
from apps.core.models import AuditLog
from apps.orders import amendment_engine as am
from apps.orders import amendments
from apps.orders.models import AmendmentState, OrderAmendment, VendorOrder
from apps.orders.tests.control import plainly
from apps.orders.tests.integration.test_amendments import (  # noqa: F401 (autouse fixture)
    CTX,
    CUSTOMER_ASKED,
    NOW,
    draft,
    key_of,
    method,
    move_to,
    owner_of,
    seed,
    tax_setup,
)

pytestmark = pytest.mark.django_db

D = Decimal


def grant(amendment: OrderAmendment, actor: Any) -> str:
    ask = amendments.amendment_passkey(amendment)
    assert ask is not None
    session = secrets.token_hex(16)
    levels.create_grant(session, actor, ask.action, ask.digest, ttl=120)
    return session


def staff_user() -> Any:
    staff = UserFactory.create(is_staff=True)
    staff.user_permissions.add(
        *Permission.objects.filter(codename__in=["view_all_vendors", "platform_support"])
    )
    return staff


# --- the passkey (CORE.25) ---------------------------------------------------------------------


def test_an_address_change_asks_a_passkey_even_when_no_money_moves() -> None:
    sub = seed()
    actor = owner_of(sub)
    amendment, _ = draft(sub, move_to("IT", "00100"), customer_request=CUSTOMER_ASKED, actor=actor)
    assert amendment is not None
    ask = amendments.amendment_passkey(amendment)
    assert getattr(ask, "action", None) == "order_amend_address", plainly(
        "an address change must be the critical action order_amend_address", f"it asks {ask}"
    )
    with pytest.raises(amendments.PasskeyRequired, match="delivery address"):
        amendments.apply_amendment(amendment, actor=actor, ctx=CTX, now=NOW)
    session = grant(amendment, actor)
    amendments.apply_amendment(amendment, actor=actor, ctx=CTX, now=NOW, passkey_session=session)
    sub.refresh_from_db()
    assert amendment.state == AmendmentState.APPLIED
    assert sub.current_version is not None
    assert sub.current_version.shipping_address is not None
    assert sub.current_version.shipping_address["line1"] == "2 Via Nuova"


def test_a_staff_refund_spends_the_order_amend_cancel_grant() -> None:
    sub = seed()
    method(sub.vendor, "economy", "8.00", 5)
    staff = staff_user()
    amendment, _ = draft(
        sub,
        am.ChangeCarrier(am.ShippingOffer("economy", "", D("0"))),
        actor=staff,
        staff_note="customer called",
    )
    assert amendment is not None
    assert amendment.preview["refund"] == "2.44"
    ask = amendments.amendment_passkey(amendment)
    assert getattr(ask, "action", None) == "order_amend_cancel", plainly(
        "a refund made by platform staff must ask the order_amend_cancel passkey",
        f"it asks {ask}",
    )


def test_a_carrier_at_the_same_price_is_an_everyday_action() -> None:
    sub = seed()
    method(sub.vendor, "express", "10.00", 5)
    actor = owner_of(sub)
    amendment, _ = draft(
        sub, am.ChangeCarrier(am.ShippingOffer("express", "", D("0"))), actor=actor
    )
    assert amendment is not None
    assert amendments.amendment_passkey(amendment) is None, plainly(
        "a carrier change at the same price moves no money and needs no passkey",
        "a passkey was asked",
    )
    amendments.apply_amendment(amendment, actor=actor, ctx=CTX, now=NOW)
    assert amendment.state == AmendmentState.APPLIED


def test_a_grant_is_bound_to_the_shop() -> None:
    sub = seed()
    actor = owner_of(sub)
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), actor=actor)
    assert amendment is not None
    ask = amendments.amendment_passkey(amendment)
    assert ask is not None
    other = seed()
    twin, _ = draft(other, am.RemoveLine(key_of(other, "SKU-B")))
    assert twin is not None
    twin_ask = amendments.amendment_passkey(twin)
    assert getattr(twin_ask, "digest", None) != ask.digest, plainly(
        "two amendments of two shops must never share a passkey digest", "they did"
    )


# --- editing a draft ---------------------------------------------------------------------------


def test_an_edit_bumps_the_revision_and_changes_the_digest() -> None:
    sub = seed()
    actor = owner_of(sub)
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), actor=actor)
    assert amendment is not None
    first = amendments.amendment_passkey(amendment)
    amendments.revise_amendment(
        amendment, [am.RemoveLine(key_of(sub, "SKU-B"))], actor=actor, ctx=CTX, now=NOW
    )
    amendment.refresh_from_db()
    second = amendments.amendment_passkey(amendment)
    assert amendment.revision == 2
    assert getattr(first, "digest", 1) != getattr(second, "digest", 1), plainly(
        "an edit of the draft, even to the same content, must void the earlier passkey grant",
        "the digest stayed the same",
    )


def test_an_edit_dry_run_writes_nothing() -> None:
    sub = seed()
    actor = owner_of(sub)
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")), actor=actor)
    assert amendment is not None
    changes = amendments.revise_amendment(
        amendment,
        [am.ChangePrice(key_of(sub, "SKU-A"), D("45.00"))],
        actor=actor,
        ctx=CTX,
        now=NOW,
        dry_run=True,
    )
    assert changes.changes
    amendment.refresh_from_db()
    assert amendment.revision == 1
    assert amendment.preview["refund"] == "36.60"
    assert not AuditLog.objects.filter(action="orders.amendment_edited").exists()


def test_staff_cannot_edit_a_shop_draft() -> None:
    sub = seed()
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert amendment is not None
    with pytest.raises(amendments.AmendmentRefused, match="started by the shop"):
        amendments.revise_amendment(
            amendment,
            [am.ChangeCarrier(am.ShippingOffer("standard", "", D("0")))],
            actor=staff_user(),
            ctx=CTX,
            staff_note="customer called",
            now=NOW,
        )


# --- closing an open amendment from elsewhere --------------------------------------------------


def test_a_draft_idle_for_an_hour_is_discarded() -> None:
    idle_sub, busy_sub = seed(), seed()
    idle, _ = draft(idle_sub, am.RemoveLine(key_of(idle_sub, "SKU-B")))
    busy, _ = draft(busy_sub, am.RemoveLine(key_of(busy_sub, "SKU-B")))
    assert idle is not None
    assert busy is not None
    moment = timezone.now()
    OrderAmendment.objects.filter(pk=idle.pk).update(updated_at=moment - dt.timedelta(minutes=61))
    OrderAmendment.objects.filter(pk=busy.pk).update(updated_at=moment - dt.timedelta(minutes=59))
    assert amendments.discard_idle_drafts(now=moment) == 1
    idle.refresh_from_db()
    busy.refresh_from_db()
    assert (idle.state, busy.state) == ("discarded", "draft"), plainly(
        "a draft idle for 61 minutes must be discarded and one idle for 59 kept",
        f"they are {idle.state} and {busy.state}",
    )
    entry = AuditLog.objects.get(action="orders.amendment_discarded")
    assert (entry.after or {})["closed_by"] == "idle"


def test_the_idle_and_hold_defaults() -> None:
    assert am.DRAFT_IDLE_MINUTES == 60, plainly(
        "a draft must be discarded after 60 idle minutes", f"the default is {am.DRAFT_IDLE_MINUTES}"
    )
    assert am.DEFAULT_CANCEL_HOLD_HOURS == 24, plainly(
        "shipping must be held 24 hours after a partial unavailability",
        f"the default is {am.DEFAULT_CANCEL_HOLD_HOURS}",
    )


def test_a_withdrawn_package_cannot_be_amended(settings: Any) -> None:
    settings.ORDER_WITHDRAWAL_HOOKS = [
        "apps.orders.tests.integration.test_amendments_apply.always_withdrawn"
    ]
    sub = seed()
    with pytest.raises(amendments.AmendmentRefused, match="withdrawn") as refused:
        draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert not OrderAmendment.objects.filter(vendor_order=sub).exists(), plainly(
        "no draft may be written on a package with a registered withdrawal",
        f"one was written ({refused.value})",
    )


def always_withdrawn(vendor_order: VendorOrder) -> bool:
    return True


def test_a_withdrawal_closes_the_open_amendment_as_withdrawn() -> None:
    sub = seed()
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert amendment is not None
    assert amendments.withdraw_open_amendment(sub, ctx=CTX) is not None
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.WITHDRAWN, plainly(
        "a withdrawal registered while an amendment is open must close it as withdrawn",
        f"it is {amendment.state}",
    )
    closed = AuditLog.objects.get(action="orders.amendment_withdrawn")
    assert (closed.after or {})["closed_by"] == "withdrawal"
    assert amendments.withdraw_open_amendment(sub, ctx=CTX) is None


def test_a_refund_closes_the_open_amendment_as_superseded() -> None:
    sub = seed()
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert amendment is not None
    amendments.supersede_open_amendment(sub, cause="refund", ctx=CTX)
    amendment.refresh_from_db()
    assert amendment.state == AmendmentState.SUPERSEDED


def test_the_cancel_the_rest_offer_needs_an_offer() -> None:
    sub = seed()
    with pytest.raises(amendments.AmendmentRefused, match="no offer"):
        amendments.cancel_rest_of_package(sub, actor=None, verified_guest=True, ctx=CTX)


def test_the_digest_changes_with_the_shop_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    sub = seed()
    amendment, _ = draft(sub, am.RemoveLine(key_of(sub, "SKU-B")))
    assert amendment is not None
    mine = amendments.amendment_passkey(amendment)
    monkeypatch.setattr(levels, "shop_key", lambda shop: "vendors.vendor:other")
    other = amendments.amendment_passkey(amendment)
    assert getattr(mine, "digest", 1) != getattr(other, "digest", 1), plainly(
        "the same amendment confirmed for another shop must have another digest",
        "the digest stayed the same",
    )


# --- the operator's settings (ORD.20 part 3) ----------------------------------------------------


def _configure(**values: Any) -> None:
    from apps.core.models import PlatformOperator
    from apps.core.services import configure_platform_operator, current_operator

    latest = current_operator(NOW)
    skip = {"id", "public_id", "created_at", "updated_at", "valid_to", "logo"}
    new = PlatformOperator(
        **{
            f.name: getattr(latest, f.name)
            for f in PlatformOperator._meta.concrete_fields
            if f.name not in skip
        }
    )
    new.valid_from = NOW - dt.timedelta(days=1)
    for name, value in values.items():
        setattr(new, name, value)
    configure_platform_operator(new, ctx=CTX)


def test_the_code_defaults_are_the_configuration_defaults() -> None:
    from apps.core import models as core

    assert core.DEFAULT_AMENDMENT_ANSWER_HOURS == am.DEFAULT_ANSWER_HOURS == 72, plainly(
        "the answer time must default to 72 hours in the code and the configuration",
        "they differ",
    )
    assert core.DEFAULT_AMENDMENT_COMPARABLE_DELAY_DAYS == am.DEFAULT_COMPARABLE_DELAY_DAYS == 2
    assert core.DEFAULT_AMENDMENT_CANCEL_HOLD_HOURS == am.DEFAULT_CANCEL_HOLD_HOURS == 24
    rules = amendments.amendment_settings(NOW)
    found = (rules.answer_hours, rules.comparable_delay_days, rules.cancel_hold_hours)
    assert found == (72, 2, 24), plainly(
        "a fresh configuration must give 72 h, 2 days and 24 h", f"it gives {rules}"
    )


def test_the_comparable_delay_is_read_from_the_configuration() -> None:
    sub = seed()
    method(sub.vendor, "slower", "10.00", 7)  # 2 days slower than the 5 of the seed
    _configure(amendment_comparable_delay_days=1)
    amendment, _ = draft(sub, am.ChangeCarrier(am.ShippingOffer("slower", "", D("0"))))
    assert amendment is not None
    assert amendment.kind == "substantial", plainly(
        "with a comparable delay of 1 day, a carrier 2 days slower must be substantial",
        f"it was classified {amendment.kind}",
    )


def test_the_shipping_hold_is_read_from_the_configuration() -> None:
    _configure(amendment_cancel_hold_hours=6)
    rules = amendments.amendment_settings(NOW)
    assert rules.cancel_hold_hours == 6, plainly("the hold must be 6 hours", f"it is {rules}")
