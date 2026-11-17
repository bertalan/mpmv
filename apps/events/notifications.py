"""Event emails through the ARC.16 outbox (EVT.11, spec 15 "Emails").

To the vendor's owner members, in their language: approved, changes requested (with the
moderator's comment and a CORE.15 secure link to the event form), hidden (with the reason, the
DSA notice reference and, when the CMP.5 flow issues the statement of reasons with it, the link
to it) and restored. To the "Event moderators": a daily digest of the events
awaiting review, nothing when the queue is empty. Each email is queued in the caller's
transaction with an idempotency key of event, transition, version and recipient, so a repeated
signal or retry queues nothing more. Wagtail's own moderation emails to vendors are off
(`switch_off_wagtail_vendor_emails`).
"""

import datetime as dt
from collections.abc import Sequence
from typing import Any

from django.conf import settings
from django.urls import reverse
from wagtail.admin import signal_handlers
from wagtail.models import WorkflowState

from apps.accounts.models import User
from apps.accounts.services import (
    ActionPurpose,
    register_action_purpose,
    send_action_link_email,
)
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.services import enqueue_email
from apps.vendors.engine import VendorAction
from apps.vendors.models import VendorMember
from apps.vendors.services import has_vendor_permission

from .models import EVENT_MODERATORS, EventPage

CHANGES_PURPOSE = "events.changes_requested"
APPROVED = "events.event_approved"
CHANGES_REQUESTED = "events.event_changes_requested"
HIDDEN = "events.event_hidden"
RESTORED = "events.event_restored"
DIGEST = "events.review_digest"


def _base_url() -> str:
    return str(settings.SITE_BASE_URL).rstrip("/")


def _form_url(page: EventPage) -> str:
    return _base_url() + reverse("manager:shop:event_edit", args=[page.public_id])


def statements_url(notice_ref: str = "") -> str:
    """The vendor's page of statements of reasons, at the statement that follows `notice_ref`."""
    url = _base_url() + reverse("manager:shop:statements")
    return f"{url}#notice-{notice_ref}" if notice_ref else url


def owners(page: EventPage) -> list[User]:
    """The owner members of the event's vendor who can be emailed; none for a platform event."""
    if page.vendor_id is None:
        return []
    members = (
        VendorMember.objects.filter(vendor_id=page.vendor_id, role=VendorMember.Role.OWNER)
        .select_related("user")
        .order_by("id")
    )
    return [m.user for m in members if m.user.is_active and m.user.email]


def _key(transition: str, page: EventPage, version: object, user: User) -> str:
    return f"events:{transition}:{page.public_id}:{version}:{user.public_id}"


def _context(page: EventPage, **extra: Any) -> dict[str, Any]:
    return {"title": page.title, "event_url": _form_url(page), **extra}


def notify_vendor(
    page: EventPage, template_key: str, version: object, **extra: Any
) -> list[ChangeSet]:
    """Queue `template_key` to every owner of the event's vendor, once per `version`."""
    return [
        enqueue_email(
            recipient=user.email,
            template_key=template_key,
            language=user.preferred_language,
            context=_context(page, **extra),
            idempotency_key=_key(template_key, page, version, user),
        )
        for user in owners(page)
    ]


def notify_changes_requested(
    page: EventPage, task_state_id: int, state_id: int, comment: str, *, ctx: AuditContext
) -> None:
    """The comment, and a secure link to the form that stops working once the vendor resubmits."""
    for user in owners(page):
        send_action_link_email(
            purpose=CHANGES_PURPOSE,
            object_key=str(page.public_id),
            version=state_id,
            template_key=CHANGES_REQUESTED,
            context=_context(page, comment=comment),
            ctx=ctx,
            recipient=user,
            idempotency_key=_key(CHANGES_REQUESTED, page, task_state_id, user),
        )


def send_digest(pages: Sequence[EventPage], *, day: dt.date, dry_run: bool = False) -> ChangeSet:
    """The daily digest to each active "Event moderators" member; nothing for an empty queue."""
    changes = ChangeSet()
    if not pages:
        return changes
    moderators = (
        User.objects.filter(groups__name=EVENT_MODERATORS, is_active=True)
        .exclude(email="")
        .distinct()
        .order_by("pk")
    )
    items = [
        {"title": page.title, "vendor": page.vendor.display_name if page.vendor else ""}
        for page in pages
    ]
    for user in moderators:
        sent = enqueue_email(
            recipient=user.email,
            template_key=DIGEST,
            language=user.preferred_language,
            context={
                "count": len(items),
                "items": items,
                "review_url": _base_url() + reverse("events_admin:review"),
            },
            idempotency_key=f"events:digest:{day.isoformat()}:{user.public_id}",
            dry_run=dry_run,
        )
        changes.changes.extend(sent.changes)
    return changes


# --- The secure link (CORE.15) and Wagtail's own emails


def _state_version(object_key: str) -> object | None:
    page = EventPage.objects.filter(public_id=object_key).first()
    state = page.current_workflow_state if page is not None else None
    if state is None or state.status != WorkflowState.STATUS_NEEDS_CHANGES:
        return None
    pk: int = state.pk
    return pk


def _may_act(object_key: str, user: User) -> bool:
    page = EventPage.objects.filter(public_id=object_key).select_related("vendor").first()
    return bool(
        page is not None
        and page.vendor is not None
        and has_vendor_permission(user, page.vendor, VendorAction.EVENTS)
    )


def _form_path(object_key: str) -> str:
    return reverse("manager:shop:event_edit", args=[object_key])


def register_changes_purpose() -> None:
    """The "changes requested" link opens the event form while the review waits for the vendor."""
    register_action_purpose(
        ActionPurpose(
            name=CHANGES_PURPOSE,
            version_of=_state_version,
            url_of=_form_path,
            may_act=_may_act,
        )
    )


def switch_off_wagtail_vendor_emails() -> None:
    """Wagtail mails the submitter on approval and rejection: for an event the outbox does it
    (vendors are not admin users). Staff's "submitted" notifications stay."""
    for notifier in (
        signal_handlers.workflow_approval_email_notifier,
        signal_handlers.workflow_rejection_email_notifier,
    ):
        original = notifier.can_handle

        def can_handle(instance: Any, *, _original: Any = original, **kwargs: Any) -> bool:
            content = getattr(instance, "content_object", None)
            content = getattr(content, "specific", content)
            return not isinstance(content, EventPage) and bool(_original(instance, **kwargs))

        notifier.can_handle = can_handle
