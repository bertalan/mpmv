"""EVT.11: event emails through the ARC.16 outbox and the DSA notice target (spec 15 "Emails").

Done when: each transition queues exactly one email in the vendor's language; the digest is not
sent for an empty queue; a CMP.5 notice on an event ends with the page unpublished, a statement
of reasons and the event gone from pages and feeds.
"""

import datetime as dt
import re
from typing import Any
from urllib.parse import urlsplit

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from wagtail.admin import signal_handlers
from wagtail.models import WorkflowState

from apps.accounts.models import User
from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.core import services as core_services
from apps.core.models import OutboxEmail
from apps.events import services
from apps.events.models import EVENT_MODERATORS, EventPage
from apps.events.services import (
    hide_event,
    restore_event,
    save_event_draft,
    submit_event,
)
from apps.events.tests.factories import STARTS_AT, EventPageFactory
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory
from tests.i18n import other_language

pytestmark = pytest.mark.django_db

KEYS = (
    "events.event_approved",
    "events.event_changes_requested",
    "events.event_hidden",
    "events.event_restored",
    "events.review_digest",
)


def _mails(key: str | None = None) -> list[OutboxEmail]:
    rows = OutboxEmail.objects.filter(template_key__in=KEYS if key is None else [key])
    return list(rows.order_by("pk"))


def _owner(language: str | None = None) -> User:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    member: VendorMember = VendorMemberFactory.create(vendor=vendor)
    if language:
        User.objects.filter(pk=member.user.pk).update(preferred_language=language)
        member.user.refresh_from_db()
    return member.user


def _submitted(owner: User) -> EventPage:
    vendor = VendorMember.objects.get(user=owner).vendor
    data: dict[str, Any] = {
        "title": "Spring trade fair",
        "summary": "Meet the makers.",
        "event_type": EventPage.EventType.FAIR,
        "starts_at": STARTS_AT,
        "ends_at": STARTS_AT + dt.timedelta(hours=8),
        "timezone": "Europe/Rome",
        "attendance": EventPage.Attendance.IN_PERSON,
        "venue_name": "Exhibition centre",
        "city": "Milano",
        "country": "IT",
    }
    page = save_event_draft(owner, data, vendor=vendor).page
    assert page is not None
    submit_event(owner, page)
    return EventPage.objects.get(pk=page.pk)


def _moderator() -> User:
    user: User = UserFactory.create(superuser=True)
    user.groups.add(Group.objects.get(name=EVENT_MODERATORS))
    return user


def _act(page: EventPage, moderator: User, action: str, comment: str = "") -> None:
    state = EventPage.objects.get(pk=page.pk).current_workflow_state
    assert state is not None
    task_state = state.current_task_state
    task_state.task.specific.on_action(task_state, moderator, action, comment=comment)


def _staff() -> User:
    user: User = UserFactory.create(superuser=True)
    return user


def test_approval_queues_one_email_in_the_vendors_language() -> None:
    owner = _owner(other_language())
    page = _submitted(owner)
    assert _mails() == []
    moderator = _moderator()
    _act(page, moderator, "approve")
    mail = _mails()[0]
    assert len(_mails()) == 1
    assert (mail.template_key, mail.recipient, mail.language) == (
        "events.event_approved",
        owner.email,
        other_language(),
    )
    rendered = core_services.render_email(mail.template_key, mail.language, mail.context_data)
    assert "Spring trade fair" in rendered.subject + rendered.text_body


def test_a_repeated_decision_signal_queues_nothing_more() -> None:
    owner = _owner()
    page = _submitted(owner)
    _act(page, _moderator(), "approve")
    state = WorkflowState.objects.get(object_id=str(page.pk))
    services.record_review_decision(state, None, approved=True)
    assert len(_mails("events.event_approved")) == 1


def test_changes_requested_queues_one_email_with_the_comment_and_a_secure_link() -> None:
    owner = _owner()
    page = _submitted(owner)
    _act(page, _moderator(), "reject", comment="Give the booth number.")
    (mail,) = _mails()
    assert mail.template_key == "events.event_changes_requested"
    rendered = core_services.render_email(mail.template_key, mail.language, mail.context_data)
    assert "Give the booth number." in rendered.text_body
    assert _link_token(rendered.text_body)
    assert "Spring trade fair" in rendered.subject + rendered.text_body


def _link_token(text: str) -> str:
    found = re.search(r"/account/link/([^/\s]+)/", text)
    assert found, text
    return found.group(1)


def _changes_requested_mail(page: EventPage, *, count: int) -> OutboxEmail:
    mails = _mails("events.event_changes_requested")
    assert len(mails) == count
    return mails[-1]


def _token_of(mail: OutboxEmail) -> str:
    rendered = core_services.render_email(mail.template_key, mail.language, mail.context_data)
    return _link_token(rendered.text_body)


def _open(client: Client, token: str) -> Any:
    return client.post(reverse("action_link:continue"), {"token": token})


def test_the_changes_link_opens_the_event_form_once_for_the_owner() -> None:
    owner = _owner()
    page = _submitted(owner)
    _act(page, _moderator(), "reject", comment="Give the booth number.")
    token = _token_of(_changes_requested_mail(page, count=1))
    TOTPDeviceFactory.create(user=owner)  # a vendor owner has a second factor
    form = reverse("manager:shop:event_edit", args=[page.public_id])
    response = _open(Client(), token)  # the owner opens it from the email
    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == form
    again = _open(Client(), token)  # single use: a second session is refused
    assert urlsplit(again["Location"]).path != form


def test_the_changes_link_is_dead_after_the_vendor_resubmits() -> None:
    owner = _owner()
    page = _submitted(owner)
    _act(page, _moderator(), "reject", comment="Give the booth number.")
    token = _token_of(_changes_requested_mail(page, count=1))
    submit_event(owner, EventPage.objects.get(pk=page.pk))
    TOTPDeviceFactory.create(user=owner)
    response = _open(Client(), token)
    form = reverse("manager:shop:event_edit", args=[page.public_id])
    assert urlsplit(response["Location"]).path != form


def test_hide_and_restore_queue_one_email_each() -> None:
    owner = _owner()
    page = _live_of(owner)
    staff = _staff()
    hide_event(staff, page, "Misleading dates.")
    (hidden,) = _mails()
    assert hidden.template_key == "events.event_hidden"
    assert (
        "Misleading dates."
        in core_services.render_email(
            hidden.template_key, hidden.language, hidden.context_data
        ).text_body
    )
    restore_event(staff, EventPage.objects.get(pk=page.pk))
    assert [m.template_key for m in _mails()] == ["events.event_hidden", "events.event_restored"]


def _live_of(owner: User) -> EventPage:
    vendor = VendorMember.objects.get(user=owner).vendor
    page: EventPage = EventPageFactory.create(vendor=vendor)
    page.save_revision().publish()
    return EventPage.objects.get(pk=page.pk)


def test_dry_run_queues_no_email() -> None:
    owner = _owner()
    page = _live_of(owner)
    hide_event(_staff(), page, "Misleading dates.", dry_run=True)
    assert _mails() == []


def test_a_platform_event_has_nobody_to_email() -> None:
    page: EventPage = EventPageFactory.create(platform=True)
    page.save_revision().publish()
    hide_event(_staff(), EventPage.objects.get(pk=page.pk), "Duplicate.")
    assert _mails() == []


def test_wagtails_own_moderation_emails_to_the_vendor_are_off(mailoutbox: list[Any]) -> None:
    owner = _owner()
    page = _submitted(owner)
    _act(page, _moderator(), "approve")
    assert [m for m in mailoutbox if owner.email in m.to] == []
    state = WorkflowState.objects.get(object_id=str(page.pk))
    assert not signal_handlers.workflow_approval_email_notifier.can_handle(state)


def test_the_digest_is_not_sent_for_an_empty_queue() -> None:
    _moderator()
    assert services.send_review_digest().is_empty
    assert _mails("events.review_digest") == []


def test_the_digest_goes_once_a_day_to_each_moderator_when_events_wait() -> None:
    _submitted(_owner())
    moderator = _moderator()
    services.send_review_digest()
    services.send_review_digest()
    (mail,) = _mails("events.review_digest")
    assert mail.recipient == moderator.email


# --- Review of 2026-11-17


def _text(mail: OutboxEmail) -> str:
    return core_services.render_email(mail.template_key, mail.language, mail.context_data).text_body


def test_hiding_with_a_statement_links_to_it() -> None:
    page = _live_of(_owner())
    hide_event(_staff(), page, "Scam.", dsa_notice_ref="N-1", statement_follows=True)
    (mail,) = _mails()
    text = _text(mail)
    assert reverse("manager:shop:statements") + "#notice-N-1" in text
    assert "separate email" not in text


def test_hiding_without_a_statement_promises_nothing() -> None:
    page = _live_of(_owner())
    hide_event(_staff(), page, "Scam.", dsa_notice_ref="N-1")
    (mail,) = _mails()
    text = _text(mail)
    assert "N-1" in text
    assert "statement" not in text.lower()
    assert "separate email" not in text


def test_hide_and_restore_are_staff_only() -> None:
    owner = _owner()
    page = _live_of(owner)
    with pytest.raises(PermissionDenied):
        hide_event(owner, page, "No.")
    hide_event(_staff(), page, "Yes.")
    with pytest.raises(PermissionDenied):
        restore_event(owner, EventPage.objects.get(pk=page.pk))
    assert [m.template_key for m in _mails()] == ["events.event_hidden"]


def test_a_second_hide_and_restore_cycle_sends_new_emails() -> None:
    page = _live_of(_owner())
    staff = _staff()
    for _ in range(2):
        hide_event(staff, EventPage.objects.get(pk=page.pk), "Misleading dates.")
        restore_event(staff, EventPage.objects.get(pk=page.pk))
    assert [m.template_key for m in _mails()] == [
        "events.event_hidden",
        "events.event_restored",
    ] * 2


def test_only_active_owner_members_with_an_email_are_written_to() -> None:
    owner = _owner()
    vendor = VendorMember.objects.get(user=owner).vendor
    VendorMemberFactory.create(vendor=vendor, role=VendorMember.Role.STAFF)
    gone = VendorMemberFactory.create(vendor=vendor)
    User.objects.filter(pk=gone.user.pk).update(is_active=False)
    blank = VendorMemberFactory.create(vendor=vendor)
    User.objects.filter(pk=blank.user.pk).update(email="")
    hide_event(_staff(), _live_of(owner), "Misleading dates.")
    assert [m.recipient for m in _mails()] == [owner.email]


def test_the_digest_skips_a_moderator_without_an_email() -> None:
    _submitted(_owner())
    silent = _moderator()
    User.objects.filter(pk=silent.pk).update(email="")
    kept = _moderator()
    services.send_review_digest()
    assert [m.recipient for m in _mails("events.review_digest")] == [kept.email]


def test_wagtails_rejection_email_to_the_vendor_is_off(mailoutbox: list[Any]) -> None:
    """Guards a Wagtail upgrade: the switch-off patches the notifier's `can_handle`."""
    owner = _owner()
    page = _submitted(owner)
    _act(page, _moderator(), "reject", comment="Give the booth number.")
    assert [m for m in mailoutbox if owner.email in m.to] == []
    state = WorkflowState.objects.get(object_id=str(page.pk))
    assert not signal_handlers.workflow_rejection_email_notifier.can_handle(state)
