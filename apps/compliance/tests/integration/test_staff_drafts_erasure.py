"""ORD.32 follow-up (#757): an account erasure expires the customer's open staff-draft links.

A one-time link sent before the erasure must not open afterwards: it answers as an expired link.
Another customer's link is untouched, an erasure dry run changes nothing, and the audit entry of
the erasure holds no email and no name. While the erasure is pending (or the account is
inactive) staff can neither make a new draft nor send a link. Needs human review: orders, GDPR.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.compliance import gdpr
from apps.core.models import AuditLog, OutboxEmail
from apps.orders import engine, staff_drafts
from apps.orders import services as order_services
from apps.orders.models import StaffDraft, StaffDraftState
from apps.orders.tests.integration.test_order_again import order_of
from apps.orders.tests.integration.test_order_again import product as make_product
from apps.orders.tests.integration.test_order_again import vendor as make_vendor
from apps.orders.tests.integration.test_staff_drafts import (
    CTX,
    NOTE,
    customer,
    draft_of,
    open_with,
    sent_token,
    staff,
)

# Items in a cart need a consumer price (TAX.12, ORD.19 on main): operator, rates, tax profiles.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("consumer_prices")]


def sent_draft(user: User | None) -> tuple[StaffDraft, str]:
    draft = draft_of(order_of(user, [(make_product(make_vendor()), 1)]))
    return draft, sent_token(draft)


def test_erasure_makes_an_open_draft_link_answer_as_an_expired_link() -> None:
    _, user = customer()
    draft, token = sent_draft(user)
    guest_order = order_of(None, [(make_product(make_vendor()), 1)])
    User.objects.filter(pk=user.pk).update(email=guest_order.email)
    guest_token = sent_token(draft_of(guest_order))  # a guest order with the same email

    gdpr.anonymise_customer(user, ctx=CTX)

    assert staff_drafts.check_link(token).refusal == engine.DRAFT_LINK_EXPIRED
    assert Client().get(reverse("staff_draft_link", args=[token])).status_code == 410
    assert open_with(Client(), token).status_code == 410
    assert staff_drafts.check_link(guest_token).refusal is None  # not the account's data
    draft.refresh_from_db()
    assert draft.state == StaffDraftState.OPEN
    assert not staff_drafts.link_active(draft)


def test_another_customers_link_is_untouched() -> None:
    _, user = customer()
    _, other = customer()
    sent_draft(user)
    other_draft, other_token = sent_draft(other)
    expires_at = StaffDraft.objects.get(pk=other_draft.pk).link_expires_at

    gdpr.anonymise_customer(user, ctx=CTX)

    assert staff_drafts.check_link(other_token).refusal is None
    assert StaffDraft.objects.get(pk=other_draft.pk).link_expires_at == expires_at


def test_an_erasure_dry_run_changes_nothing() -> None:
    _, user = customer()
    draft, token = sent_draft(user)
    expires_at = StaffDraft.objects.get(pk=draft.pk).link_expires_at
    audits = AuditLog.objects.count()

    changeset = gdpr.anonymise_customer(user, ctx=CTX, dry_run=True)

    assert any(c.model == staff_drafts.DRAFT_MODEL for c in changeset.changes)
    assert StaffDraft.objects.get(pk=draft.pk).link_expires_at == expires_at
    assert staff_drafts.check_link(token).refusal is None
    assert AuditLog.objects.count() == audits


def test_the_service_dry_run_changes_nothing() -> None:
    _, user = customer()
    _, token = sent_draft(user)

    changeset = staff_drafts.expire_links_of_customer(user, dry_run=True)

    assert len(changeset.changes) == 1
    assert staff_drafts.check_link(token).refusal is None


def test_the_erasure_audit_entry_holds_no_email_and_no_name() -> None:
    _, user = customer()
    email, name = user.email, user.full_name
    draft, _ = sent_draft(user)

    gdpr.anonymise_customer(user, ctx=CTX)

    entry = AuditLog.objects.get(
        action=gdpr.ACTION_ERASURE,
        target_model=staff_drafts.DRAFT_MODEL,
        target_key=str(draft.public_id),
    )
    text = f"{entry.before} {entry.after}"
    assert entry.after is not None
    assert entry.after["link"] == "none"
    assert email not in text
    assert not name or name not in text


def test_a_draft_never_sent_is_not_in_the_changeset() -> None:
    _, user = customer()
    never_sent = draft_of(order_of(user, [(make_product(make_vendor()), 1)]))
    sent, _ = sent_draft(user)

    changeset = gdpr.anonymise_customer(user, ctx=CTX)

    keys = {c.key for c in changeset.changes if c.model == staff_drafts.DRAFT_MODEL}
    assert keys == {str(sent.public_id)}
    assert str(never_sent.public_id) not in keys


def test_a_deferred_erasure_then_its_completion_expire_the_link_once() -> None:
    _, user = customer()
    draft, token = sent_draft(user)  # its source order is paid, not concluded: erasure waits

    first = gdpr.anonymise_customer(user, ctx=CTX)
    assert not gdpr.is_anonymised(user)
    assert staff_drafts.check_link(token).refusal == engine.DRAFT_LINK_EXPIRED
    for state in ("in_fulfillment", "fulfilled", "closed"):
        order_services.transition_order(draft.source_order, state, ctx=CTX)
    second = gdpr.anonymise_customer(user, ctx=CTX)
    third = gdpr.anonymise_customer(user, ctx=CTX)

    assert gdpr.is_anonymised(user)
    drafts = [c for c in first.changes if c.model == staff_drafts.DRAFT_MODEL]
    assert [c.key for c in drafts] == [str(draft.public_id)]
    assert not [c for c in second.changes if c.model == staff_drafts.DRAFT_MODEL]
    assert third.changes == []
    entries = AuditLog.objects.filter(
        target_model=staff_drafts.DRAFT_MODEL, target_key=str(draft.public_id)
    )
    assert entries.filter(action=gdpr.ACTION_ERASURE).count() == 1


def test_no_draft_is_made_while_the_erasure_is_pending() -> None:
    _, user = customer()
    sent_draft(user)  # a paid order keeps the erasure pending
    other_order = order_of(user, [(make_product(make_vendor()), 1)])
    gdpr.anonymise_customer(user, ctx=CTX)

    with pytest.raises(staff_drafts.DraftRefused, match="erasure is in progress"):
        staff_drafts.draft_from_order(other_order, actor=staff(), note=NOTE, ctx=CTX)
    assert not StaffDraft.objects.filter(source_order=other_order).exists()


def test_no_link_is_sent_while_the_erasure_is_pending() -> None:
    _, user = customer()
    draft = draft_of(order_of(user, [(make_product(make_vendor()), 1)]))
    gdpr.anonymise_customer(user, ctx=CTX)
    mails = OutboxEmail.objects.filter(template_key=staff_drafts.LINK_EMAIL).count()

    with pytest.raises(staff_drafts.DraftRefused, match="erasure is in progress"):
        staff_drafts.send_link(draft, actor=staff(), note=NOTE, ctx=CTX)
    assert OutboxEmail.objects.filter(template_key=staff_drafts.LINK_EMAIL).count() == mails
    assert not StaffDraft.objects.get(pk=draft.pk).link_digest


def test_an_inactive_account_gets_no_draft_and_no_link() -> None:
    _, user = customer()
    draft = draft_of(order_of(user, [(make_product(make_vendor()), 1)]))
    other_order = order_of(user, [(make_product(make_vendor()), 1)])
    User.objects.filter(pk=user.pk).update(is_active=False)

    with pytest.raises(staff_drafts.DraftRefused):
        staff_drafts.send_link(draft, actor=staff(), note=NOTE, ctx=CTX)
    with pytest.raises(staff_drafts.DraftRefused):
        staff_drafts.draft_from_order(other_order, actor=staff(), note=NOTE, ctx=CTX)
