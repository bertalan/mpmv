"""EVT.3 part 2/2: the moderation UI in the Wagtail admin (spec 15 "Moderation with Wagtail
workflows", "Staff admin").

Done when: staff hide (with a reason), restore and cancel an event from the admin, each through
the services with one audit entry; moderators see the events awaiting "Event review"; staff
edit `VendorEventPolicy` and `EventSettings` through `set_vendor_policy` and
`set_event_settings`; a vendor member or another non-staff editor cannot edit or resubmit a
hidden page, and cannot publish without review while the vendor needs review.
"""

import datetime as dt
import secrets
from typing import Any

import pytest
from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse
from wagtail.models import GroupPagePermission, Site, WorkflowState

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.events.models import EVENT_MODERATORS, EventPage, EventSettings, VendorEventPolicy
from apps.events.services import hide_event, save_event_draft, submit_event
from apps.events.tests.factories import STARTS_AT, primary_index
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db


def _data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Spring trade fair",
        "summary": "Meet the makers at the spring trade fair.",
        "event_type": EventPage.EventType.FAIR,
        "starts_at": STARTS_AT,
        "ends_at": STARTS_AT + dt.timedelta(hours=8),
        "timezone": "Europe/Rome",
        "attendance": EventPage.Attendance.IN_PERSON,
        "venue_name": "Exhibition centre",
        "city": "Milano",
        "country": "IT",
    }
    data.update(changes)
    return data


def _owner() -> User:
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    member: VendorMember = VendorMemberFactory.create(vendor=vendor)
    return member.user


def _fresh(page: EventPage) -> EventPage:
    fresh: EventPage = EventPage.objects.get(pk=page.pk)
    return fresh


def _draft(owner: User, **changes: Any) -> EventPage:
    vendor = VendorMember.objects.get(user=owner).vendor
    page = save_event_draft(owner, _data(**changes), vendor=vendor).page
    assert page is not None
    return _fresh(page)


def _submitted(owner: User | None = None, **changes: Any) -> EventPage:
    owner = owner or _owner()
    page = _draft(owner, **changes)
    submit_event(owner, page)
    fresh = _fresh(page)
    assert fresh.status == EventPage.Status.PENDING
    return fresh


def _live(**changes: Any) -> EventPage:
    owner = _owner()
    page = _draft(owner, **changes)
    assert page.vendor is not None
    VendorEventPolicy.objects.create(vendor=page.vendor, mode=VendorEventPolicy.Mode.TRUSTED)
    submit_event(owner, page)
    fresh = _fresh(page)
    assert fresh.live
    return fresh


def _staff(client: Client) -> User:
    user: User = UserFactory.create(superuser=True)
    verified_login(client, user)
    return user


def _editor(*, publisher: bool = False) -> User:
    """A non-staff Wagtail editor; `publisher` also gives publish rights on the events index."""
    user: User = UserFactory.create(password=secrets.token_urlsafe(16))
    user.groups.add(Group.objects.get(name="Editors"))
    if publisher:
        group = Group.objects.create(name=f"Event publishers {secrets.token_hex(4)}")
        group.permissions.add(Permission.objects.get(codename="access_admin"))
        for codename in ("change_page", "publish_page"):
            GroupPagePermission.objects.create(
                group=group,
                page=primary_index(),
                permission=Permission.objects.get(
                    content_type__app_label="wagtailcore", codename=codename
                ),
            )
        user.groups.add(group)
    return user


def _audits(action: str) -> int:
    return AuditLog.objects.filter(action=action).count()


def _url(name: str, page: EventPage) -> str:
    return reverse(f"events_admin:{name}", args=(page.pk,))


def _edit_url(page: EventPage) -> str:
    return reverse("wagtailadmin_pages:edit", args=(page.pk,))


def _refused(response: Any) -> bool:
    """Wagtail's admin answers a PermissionDenied with a redirect to its dashboard."""
    if response.status_code == 403:
        return True
    location: str = response.get("Location", "")
    return bool(response.status_code == 302 and location == reverse("wagtailadmin_home"))


# --- Hide


def test_the_hide_form_asks_for_a_reason_with_a_label(client: Client) -> None:
    _staff(client)
    page = _live()
    response = client.get(_url("hide", page))
    assert response.status_code == 200
    html = response.content.decode()
    assert '<label class="w-field__label" for="id_reason"' in html
    assert 'name="reason"' in html
    assert "required" in html
    assert 'name="dsa_notice_ref"' in html
    assert "<script>" not in html  # CSP: no inline script
    assert "style=" not in html


def test_staff_hide_an_event_with_a_reason(client: Client) -> None:
    staff = _staff(client)
    page = _live()
    response = client.post(_url("hide", page), {"reason": "Misleading offer."})
    assert response.status_code == 302
    assert response["Location"] == _edit_url(page)
    fresh = _fresh(page)
    assert fresh.hidden_at is not None
    assert fresh.hidden_by == staff
    assert fresh.hidden_reason == "Misleading offer."
    assert not fresh.live
    assert fresh.status == EventPage.Status.HIDDEN
    assert _audits("events.event_hidden") == 1
    entry = AuditLog.objects.get(action="events.event_hidden")
    assert entry.source == AuditLog.Source.ADMIN
    assert entry.actor_id == staff.pk


def test_a_hide_without_a_reason_is_refused_with_an_error(client: Client) -> None:
    _staff(client)
    page = _live()
    response = client.post(_url("hide", page), {"reason": "   "})
    assert response.status_code == 200
    html = response.content.decode()
    assert "error-message" in html or "w-field__errors" in html
    assert 'aria-invalid="true"' in html or "aria-describedby" in html
    assert _fresh(page).hidden_at is None
    assert _audits("events.event_hidden") == 0


def test_a_hide_records_the_dsa_notice_reference(client: Client) -> None:
    _staff(client)
    page = _live()
    client.post(_url("hide", page), {"reason": "Notice upheld.", "dsa_notice_ref": "N-0042"})
    assert _fresh(page).dsa_notice_ref == "N-0042"


def test_a_non_staff_editor_cannot_hide(client: Client) -> None:
    verified_login(client, _editor())
    page = _live()
    assert _refused(client.get(_url("hide", page)))
    assert _refused(client.post(_url("hide", page), {"reason": "No."}))
    assert _fresh(page).hidden_at is None


# --- Restore and cancel


def test_staff_restore_a_hidden_event(client: Client) -> None:
    staff = _staff(client)
    page = _live()
    hide_event(staff, page, "Misleading offer.")
    assert client.get(_url("restore", page)).status_code == 200
    response = client.post(_url("restore", page))
    assert response.status_code == 302
    fresh = _fresh(page)
    assert fresh.hidden_at is None
    assert fresh.live
    assert fresh.status == EventPage.Status.PUBLISHED
    assert _audits("events.event_restored") == 1


def test_restoring_a_visible_event_shows_the_error(client: Client) -> None:
    _staff(client)
    page = _live()
    response = client.post(_url("restore", page), follow=True)
    assert "The event is not hidden." in response.content.decode()
    assert _audits("events.event_restored") == 0


def test_staff_cancel_a_live_event(client: Client) -> None:
    _staff(client)
    page = _live()
    assert client.get(_url("cancel", page)).status_code == 200
    response = client.post(_url("cancel", page))
    assert response.status_code == 302
    fresh = _fresh(page)
    assert fresh.cancelled_at is not None
    assert fresh.status == EventPage.Status.CANCELLED
    assert _audits("events.event_cancelled") == 1


def test_a_non_staff_editor_cannot_restore_or_cancel(client: Client) -> None:
    verified_login(client, _editor())
    page = _live()
    assert _refused(client.post(_url("cancel", page)))
    assert _refused(client.post(_url("restore", page)))
    assert _fresh(page).cancelled_at is None


def test_an_unknown_event_is_a_404(client: Client) -> None:
    _staff(client)
    assert client.get(reverse("events_admin:hide", args=(999_999,))).status_code == 404


# --- The staff actions in the page editor and the explorer


def test_the_edit_view_offers_hide_and_cancel_to_staff(client: Client) -> None:
    _staff(client)
    page = _live()
    html = client.get(_edit_url(page)).content.decode()
    assert _url("hide", page) in html
    assert _url("cancel", page) in html
    assert _url("restore", page) not in html


def test_the_edit_view_offers_restore_on_a_hidden_event(client: Client) -> None:
    staff = _staff(client)
    page = _live()
    hide_event(staff, page, "Misleading offer.")
    html = client.get(_edit_url(page)).content.decode()
    assert _url("restore", page) in html
    assert _url("hide", page) not in html


def test_the_explorer_lists_the_hide_action(client: Client) -> None:
    _staff(client)
    page = _live()
    url = reverse("wagtailadmin_explore", args=(primary_index().pk,))
    html = client.get(url).content.decode()
    assert _url("hide", page) in html


def test_a_non_staff_editor_is_not_offered_the_staff_actions(client: Client) -> None:
    verified_login(client, _editor())
    page = _live()
    response = client.get(_edit_url(page))
    html = response.content.decode()
    assert _url("hide", page) not in html
    assert _url("cancel", page) not in html


# --- Awaiting review


def test_moderators_see_the_events_awaiting_review(client: Client) -> None:
    staff = _staff(client)
    staff.groups.add(Group.objects.get(name=EVENT_MODERATORS))
    pending = _submitted(title="Pending fair")
    draft = _draft(_owner(), title="Draft fair")
    response = client.get(reverse("events_admin:review"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Pending fair" in html
    assert _edit_url(pending) in html
    assert "Draft fair" not in html
    assert _edit_url(draft) not in html
    assert str(pending.vendor) in html


def test_the_review_list_says_when_nothing_waits(client: Client) -> None:
    _staff(client)
    html = client.get(reverse("events_admin:review")).content.decode()
    assert "No event is awaiting review." in html


def test_a_non_moderator_editor_cannot_open_the_review_list(client: Client) -> None:
    verified_login(client, _editor())
    assert _refused(client.get(reverse("events_admin:review")))


def test_a_moderator_approves_through_the_wagtail_workflow(client: Client) -> None:
    staff = _staff(client)
    staff.groups.add(Group.objects.get(name=EVENT_MODERATORS))
    page = _submitted()
    state = page.current_workflow_state
    assert state is not None
    task_state = state.current_task_state
    url = reverse("wagtailadmin_pages:workflow_action", args=(page.pk, "approve", task_state.pk))
    response = client.post(url, {"comment": ""})
    assert response.status_code in (200, 302)
    fresh = _fresh(page)
    assert fresh.live
    assert fresh.status == EventPage.Status.PUBLISHED
    assert _audits("events.event_approved") == 1


# --- Vendor policy


def test_staff_set_a_vendor_policy(client: Client) -> None:
    _staff(client)
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    form = client.get(reverse("events_admin:policy_set"))
    assert form.status_code == 200
    assert 'for="id_vendor"' in form.content.decode()
    assert 'for="id_mode"' in form.content.decode()
    response = client.post(
        reverse("events_admin:policy_set"),
        {"vendor": str(vendor.public_id), "mode": VendorEventPolicy.Mode.ALWAYS_REVIEW},
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("events_admin:policies")
    policy = VendorEventPolicy.objects.get(vendor=vendor)
    assert policy.mode == VendorEventPolicy.Mode.ALWAYS_REVIEW
    assert _audits("events.policy_set") == 1
    listing = client.get(reverse("events_admin:policies")).content.decode()
    assert str(vendor) in listing


def test_changing_a_policy_is_one_audit_entry(client: Client) -> None:
    _staff(client)
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    VendorEventPolicy.objects.create(vendor=vendor, mode=VendorEventPolicy.Mode.TRUSTED)
    url = reverse("events_admin:policy_set") + f"?vendor={vendor.public_id}"
    assert VendorEventPolicy.Mode.TRUSTED in client.get(url).content.decode()
    client.post(url, {"vendor": str(vendor.public_id), "mode": VendorEventPolicy.Mode.DEFAULT})
    assert VendorEventPolicy.objects.get(vendor=vendor).mode == VendorEventPolicy.Mode.DEFAULT
    assert _audits("events.policy_changed") == 1


def test_an_invalid_policy_mode_shows_an_error(client: Client) -> None:
    _staff(client)
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    response = client.post(
        reverse("events_admin:policy_set"), {"vendor": str(vendor.public_id), "mode": "never"}
    )
    assert response.status_code == 200
    assert not VendorEventPolicy.objects.filter(vendor=vendor).exists()


def test_a_non_staff_editor_cannot_change_policies(client: Client) -> None:
    verified_login(client, _editor())
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    assert _refused(client.get(reverse("events_admin:policies")))
    response = client.post(
        reverse("events_admin:policy_set"),
        {"vendor": str(vendor.public_id), "mode": VendorEventPolicy.Mode.TRUSTED},
    )
    assert _refused(response)
    assert not VendorEventPolicy.objects.filter(vendor=vendor).exists()


# --- Events settings


def test_staff_change_the_events_settings(client: Client) -> None:
    _staff(client)
    site = Site.objects.get(is_default_site=True)
    url = reverse("events_admin:settings")
    form = client.get(url)
    assert form.status_code == 200
    assert 'for="id_moderation_threshold"' in form.content.decode()
    response = client.post(
        url, {"site": site.pk, "moderation_threshold": 5, "cancelled_retention_days": 14}
    )
    assert response.status_code == 302
    settings = EventSettings.for_site(site)
    assert settings.moderation_threshold == 5
    assert settings.cancelled_retention_days == 14
    assert AuditLog.objects.filter(target_model="events.eventsettings").count() == 1


def test_showing_the_settings_form_writes_nothing(client: Client) -> None:
    # A GET shows the defaults of a site without a settings row; only the POST creates it.
    _staff(client)
    site = Site.objects.get(is_default_site=True)
    EventSettings.objects.filter(site=site).delete()
    form = client.get(reverse("events_admin:settings") + f"?site={site.pk}")
    assert form.status_code == 200
    assert 'value="3"' in form.content.decode()
    assert not EventSettings.objects.filter(site=site).exists()


def test_an_invalid_threshold_is_refused(client: Client) -> None:
    _staff(client)
    site = Site.objects.get(is_default_site=True)
    response = client.post(
        reverse("events_admin:settings"),
        {"site": site.pk, "moderation_threshold": -1, "cancelled_retention_days": 14},
    )
    assert response.status_code == 200
    assert EventSettings.for_site(site).moderation_threshold == 3
    assert AuditLog.objects.filter(target_model="events.eventsettings").count() == 0


def test_a_non_staff_editor_cannot_change_the_settings(client: Client) -> None:
    verified_login(client, _editor())
    assert _refused(client.get(reverse("events_admin:settings")))


# --- The vendor-facing guard in the Wagtail admin


def test_a_vendor_member_with_wagtail_access_cannot_edit_or_resubmit_a_hidden_page(
    client: Client,
) -> None:
    owner = _owner()
    owner.groups.add(Group.objects.get(name="Editors"))
    page = _submitted(owner)
    hide_event(UserFactory.create(superuser=True), page, "Misleading offer.")
    verified_login(client, owner)
    assert client.get(_edit_url(page)).status_code in (302, 403)
    response = client.post(_edit_url(page), {"title": page.title, "action-submit": "Submit"})
    assert response.status_code in (302, 403)
    fresh = _fresh(page)
    assert fresh.hidden_at is not None
    assert not fresh.live
    assert not WorkflowState.objects.filter(
        object_id=str(page.pk), status=WorkflowState.STATUS_IN_PROGRESS
    ).exists()


def test_a_non_staff_editor_cannot_edit_a_hidden_page(client: Client) -> None:
    editor = _editor(publisher=True)
    page = _live()
    hide_event(UserFactory.create(superuser=True), page, "Misleading offer.")
    verified_login(client, editor)
    assert _refused(client.get(_edit_url(page)))
    response = client.post(_edit_url(page), {"title": page.title, "action-publish": "Publish"})
    assert _refused(response)
    assert not _fresh(page).live


def test_a_non_staff_editor_cannot_publish_without_review(client: Client) -> None:
    editor = _editor(publisher=True)
    page = _draft(_owner())
    assert page.review_needed()
    verified_login(client, editor)
    html = client.get(_edit_url(page)).content.decode()
    assert 'name="action-publish"' not in html
    response = client.post(_edit_url(page), {"title": page.title, "action-publish": "Publish"})
    assert _refused(response)
    assert not _fresh(page).live


def test_a_non_staff_editor_cannot_bulk_publish_an_event_needing_review(client: Client) -> None:
    editor = _editor(publisher=True)
    page = _draft(_owner())
    verified_login(client, editor)
    url = reverse("wagtail_bulk_action", args=("wagtailcore", "page", "publish")) + f"?id={page.pk}"
    response = client.post(url, {"include_descendants": ""})
    assert _refused(response)
    assert not _fresh(page).live


def test_staff_still_publish_directly(client: Client) -> None:
    _staff(client)
    page = _draft(_owner())
    html = client.get(_edit_url(page)).content.decode()
    assert 'name="action-publish"' in html
