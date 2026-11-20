"""ORD.32 follow-up (#757): an account erasure expires the customer's open staff-draft links.

A one-time link sent before the erasure must not open afterwards: it answers as an expired link.
Another customer's link is untouched, an erasure dry run changes nothing, and the audit entry of
the erasure holds no email and no name. Needs human review: orders, GDPR.
"""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.compliance import gdpr
from apps.core.models import AuditLog
from apps.orders import engine, staff_drafts
from apps.orders.models import StaffDraft, StaffDraftState
from apps.orders.tests.integration.test_order_again import order_of
from apps.orders.tests.integration.test_order_again import product as make_product
from apps.orders.tests.integration.test_order_again import vendor as make_vendor
from apps.orders.tests.integration.test_staff_drafts import (
    CTX,
    customer,
    draft_of,
    open_with,
    sent_token,
)

pytestmark = pytest.mark.django_db


def sent_draft(user: User | None) -> tuple[StaffDraft, str]:
    draft = draft_of(order_of(user, [(make_product(make_vendor()), 1)]))
    return draft, sent_token(draft)


def test_erasure_makes_an_open_draft_link_answer_as_an_expired_link() -> None:
    _, user = customer()
    draft, token = sent_draft(user)
    expired, expired_token = sent_draft(None)
    StaffDraft.objects.filter(pk=expired.pk).update(link_expires_at=timezone.now())

    gdpr.anonymise_customer(user, ctx=CTX)

    assert staff_drafts.check_link(token).refusal == engine.DRAFT_LINK_EXPIRED
    assert staff_drafts.check_link(expired_token).refusal == engine.DRAFT_LINK_EXPIRED
    for value in (token, expired_token):
        assert Client().get(reverse("staff_draft_link", args=[value])).status_code == 410
        assert open_with(Client(), value).status_code == 410
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
