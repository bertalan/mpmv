"""EVT.11: event emails through the ARC.16 outbox and the DSA notice target (spec 15 "Emails").

Done when: each transition queues exactly one email in the vendor's language; the digest is not
sent for an empty queue; a CMP.5 notice on an event ends with the page unpublished, a statement
of reasons and the event gone from pages and feeds.
"""

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import Group
from wagtail.admin import signal_handlers
from wagtail.models import WorkflowState

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
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
    assert "/action/" in rendered.text_body or "/link/" in rendered.text_body
    assert "Spring trade fair" in rendered.subject + rendered.text_body


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
