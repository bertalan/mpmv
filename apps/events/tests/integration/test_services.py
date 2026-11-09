"""EVT.3: the events services (spec 15 "Services", "Moderation with Wagtail workflows").

Done when: regression cases 1-4 green; every mutation writes one audit entry and `dry_run`
writes no revision; `sequence` grows on a date change and stays on a no-op publish; a hide
without a reason is refused; a hidden page cannot be resubmitted by the vendor.
"""

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from wagtail.models import Locale, Revision, Site, WorkflowState

from apps.accounts.tests.factories import UserFactory
from apps.catalog.tests.factories import CategoryFactory, ProductFactory
from apps.core.models import AuditLog
from apps.events import services
from apps.events.models import (
    EVENT_MODERATORS,
    EVENT_REVIEW,
    EventPage,
    EventSettings,
    VendorEventPolicy,
)
from apps.events.services import (
    EventRefused,
    cancel_event,
    hide_event,
    restore_event,
    save_event_draft,
    set_event_settings,
    set_vendor_policy,
    submit_event,
)
from apps.events.tests.factories import (
    STARTS_AT,
    EventIndexPageFactory,
    EventPageFactory,
    primary_index,
)
from apps.vendors.models import Vendor, VendorMember
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

User = Any


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
        "body": [{"type": "paragraph", "value": "<p>Come and see us.</p>"}],
    }
    data.update(changes)
    return data


def _owner(vendor: Vendor | None = None) -> User:
    vendor = vendor or VendorFactory.create(status=Vendor.Status.ACTIVE)
    member: VendorMember = VendorMemberFactory.create(vendor=vendor)
    return member.user


def _vendor_of(user: User) -> Vendor:
    vendor: Vendor = VendorMember.objects.get(user=user).vendor
    return vendor


def _fresh(page: EventPage) -> EventPage:
    fresh: EventPage = EventPage.objects.get(pk=page.pk)
    return fresh


@pytest.fixture
def moderator() -> User:
    user = UserFactory.create(superuser=True)
    user.groups.add(Group.objects.get(name=EVENT_MODERATORS))
    return user


@pytest.fixture
def owner() -> User:
    return _owner()


def _create(actor: User, **changes: Any) -> EventPage:
    vendor = None if actor.is_staff else _vendor_of(actor)
    changes_set = save_event_draft(actor, _data(**changes), vendor=vendor)
    page = changes_set.page
    assert page is not None
    return _fresh(page)


def _act(page: EventPage, moderator: User, action: str, comment: str = "") -> None:
    state = _fresh(page).current_workflow_state
    assert state is not None
    task_state = state.current_task_state
    task_state.task.specific.on_action(task_state, moderator, action, comment=comment)


def _vendor_key(page: EventPage) -> str:
    vendor = page.vendor
    assert vendor is not None
    return str(vendor.public_id)


def _json(value: Any) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _audits() -> int:
    return AuditLog.objects.filter(target_model="events.eventpage").count()


# --- The "Event review" workflow


def test_the_event_review_workflow_has_one_group_approval_task() -> None:
    workflow = services.ensure_event_review_workflow()
    assert workflow.name == EVENT_REVIEW
    assert workflow.active
    tasks = [task.specific for task in workflow.tasks]
    assert len(tasks) == 1
    assert [group.name for group in tasks[0].groups.all()] == [EVENT_MODERATORS]
    assert services.ensure_event_review_workflow() == workflow  # idempotent


# --- save_event_draft


def test_a_vendor_creates_a_draft_through_a_revision(owner: User) -> None:
    page = _create(owner)
    assert page.vendor == _vendor_of(owner)
    assert page.status == EventPage.Status.DRAFT
    assert not page.live
    assert page.locale == Locale.get_default()
    assert page.original_locale == page.locale
    assert page.get_parent().specific == primary_index()
    assert page.slug == "spring-trade-fair"
    revision = page.get_latest_revision()
    assert revision is not None
    assert revision.user == owner


def test_an_edit_of_a_live_event_leaves_the_live_page_untouched(owner: User) -> None:
    page = _create(owner)
    page.get_latest_revision().publish(skip_permission_checks=True)
    save_event_draft(owner, {"title": "Changed title"}, _fresh(page))
    live = _fresh(page)
    assert live.title == "Spring trade fair"
    assert live.get_latest_revision_as_object().title == "Changed title"
    assert live.has_unpublished_changes


def test_save_writes_one_audit_entry(owner: User) -> None:
    page = _create(owner)
    assert _audits() == 1
    entry = AuditLog.objects.get(target_model="events.eventpage")
    assert entry.action == "events.event_created"
    assert entry.vendor_key == _vendor_key(page)
    save_event_draft(owner, {"summary": "Another summary."}, page)
    assert _audits() == 2
    latest = AuditLog.objects.filter(target_model="events.eventpage").first()
    assert latest is not None
    assert latest.action == "events.event_draft_saved"
    assert _json(latest.before)["summary"] != _json(latest.after)["summary"]


def test_a_dry_run_writes_no_revision_and_no_audit(owner: User) -> None:
    page = _create(owner)
    revisions, audits = Revision.objects.count(), _audits()
    changes = save_event_draft(owner, {"title": "Dry title"}, page, dry_run=True)
    assert changes.to_dict()["changes"][0]["fields"]["title"] == ["Spring trade fair", "Dry title"]
    assert Revision.objects.count() == revisions
    assert _audits() == audits
    assert _fresh(page).get_latest_revision_as_object().title == "Spring trade fair"


def test_a_dry_run_create_writes_nothing(owner: User) -> None:
    pages = EventPage.objects.count()
    changes = save_event_draft(owner, _data(), vendor=_vendor_of(owner), dry_run=True)
    assert changes.summary()["create"] == 1
    assert changes.page is None
    assert EventPage.objects.count() == pages
    assert _audits() == 0


def test_an_invalid_draft_is_refused_with_field_errors(owner: User) -> None:
    with pytest.raises(ValidationError) as excinfo:
        save_event_draft(
            owner,
            _data(ends_at=STARTS_AT - dt.timedelta(hours=1)),
            vendor=_vendor_of(owner),
        )
    assert "ends_at" in excinfo.value.message_dict
    assert _audits() == 0


def test_an_unknown_field_is_refused(owner: User) -> None:
    with pytest.raises(ValidationError) as excinfo:
        save_event_draft(owner, _data(sequence=7), vendor=_vendor_of(owner))
    assert "sequence" in excinfo.value.message_dict


def test_a_vendor_cannot_use_a_block_outside_the_vendor_safe_set(owner: User) -> None:
    body = [{"type": "raw_html", "value": "<script></script>"}]
    with pytest.raises(ValidationError) as excinfo:
        save_event_draft(owner, _data(body=body), vendor=_vendor_of(owner))
    assert "body" in excinfo.value.message_dict


def test_a_vendor_cannot_link_a_script_url(owner: User) -> None:
    body = [{"type": "paragraph", "value": '<p><a href="javascript:alert(1)">x</a></p>'}]
    with pytest.raises(ValidationError) as excinfo:
        save_event_draft(owner, _data(body=body), vendor=_vendor_of(owner))
    assert "body" in excinfo.value.message_dict


def test_a_vendor_event_links_only_its_own_products(owner: User) -> None:
    vendor = _vendor_of(owner)
    mine = ProductFactory.create(vendor=vendor)
    theirs = ProductFactory.create()
    category = CategoryFactory.create()
    page = _create(owner, products=[mine.pk], categories=[category.pk])
    linked = page.get_latest_revision_as_object()
    assert [p.pk for p in linked.products.all()] == [mine.pk]
    with pytest.raises(ValidationError) as excinfo:
        save_event_draft(owner, {"products": [mine.pk, theirs.pk]}, page)
    assert "products" in excinfo.value.message_dict


def test_a_platform_event_may_link_any_product() -> None:
    staff = UserFactory.create(platform_staff=True)
    product = ProductFactory.create()
    page = _create(staff, products=[product.pk])
    assert page.vendor is None
    assert [p.pk for p in page.get_latest_revision_as_object().products.all()] == [product.pk]


def test_a_vendor_cannot_edit_another_vendors_event(owner: User) -> None:
    theirs = EventPageFactory.create()
    with pytest.raises(Http404):
        save_event_draft(owner, {"title": "Mine now"}, theirs)


def test_a_vendor_cannot_create_an_event_for_another_vendor(owner: User) -> None:
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    with pytest.raises(PermissionDenied):
        save_event_draft(owner, _data(), vendor=other)


def test_only_staff_create_platform_events(owner: User) -> None:
    with pytest.raises(PermissionDenied):
        save_event_draft(owner, _data(), vendor=None)


def test_a_customer_cannot_create_events() -> None:
    customer = UserFactory.create()
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    with pytest.raises(PermissionDenied):
        save_event_draft(customer, _data(), vendor=vendor)


def test_a_taken_slug_gets_a_suffix(owner: User) -> None:
    first = _create(owner)
    second = _create(owner)
    assert first.slug == "spring-trade-fair"
    assert second.slug == "spring-trade-fair-2"


def test_a_draft_in_another_language_goes_under_that_index(owner: User) -> None:
    primary_index()
    locale = EventIndexPageFactory.create().locale  # under a home page in another language
    assert locale != Locale.get_default()
    changes = save_event_draft(owner, _data(), vendor=_vendor_of(owner), locale=locale)
    assert changes.page is not None
    page = _fresh(changes.page)
    assert page.locale == locale
    assert page.original_locale == locale
    assert page.get_parent().locale == locale


# --- Regression case 1: the moderation threshold


def test_regression_1_the_first_three_events_go_through_review(
    owner: User, moderator: User
) -> None:
    pages = [_create(owner, title=f"Fair {n}") for n in range(1, 4)]
    for page in pages:
        submit_event(owner, page)
        fresh = _fresh(page)
        assert fresh.status == EventPage.Status.PENDING
        assert not fresh.live
        assert fresh.current_workflow_state.workflow.name == EVENT_REVIEW
    for page in pages:
        _act(page, moderator, "approve")
        assert _fresh(page).live
        assert _fresh(page).status == EventPage.Status.PUBLISHED

    fourth = _create(owner, title="Fair 4")
    submit_event(owner, fourth)
    fresh = _fresh(fourth)
    assert fresh.live
    assert fresh.status == EventPage.Status.PUBLISHED
    assert not WorkflowState.objects.filter(object_id=str(fourth.pk)).exists()


# --- Regression case 2: changes requested do not count


def test_regression_2_a_rejected_submission_does_not_count(owner: User, moderator: User) -> None:
    rejected = _create(owner, title="Rejected fair")
    submit_event(owner, rejected)
    _act(rejected, moderator, "reject", comment="Give the booth number.")
    assert _fresh(rejected).status == EventPage.Status.DRAFT

    pages = [_create(owner, title=f"Fair {n}") for n in range(1, 4)]
    for page in pages[:2]:
        submit_event(owner, page)
        _act(page, moderator, "approve")
    submit_event(owner, pages[2])
    assert _fresh(pages[2]).status == EventPage.Status.PENDING  # two approved: still reviewed
    _act(pages[2], moderator, "approve")

    after = _create(owner, title="After three")
    submit_event(owner, after)
    assert _fresh(after).status == EventPage.Status.PUBLISHED


def test_a_resubmission_after_requested_changes_resumes_the_review(
    owner: User, moderator: User
) -> None:
    page = _create(owner)
    submit_event(owner, page)
    _act(page, moderator, "reject", comment="Fix the date.")
    save_event_draft(owner, {"summary": "Fixed."}, _fresh(page))
    submit_event(owner, _fresh(page))
    fresh = _fresh(page)
    assert fresh.status == EventPage.Status.PENDING
    assert WorkflowState.objects.filter(object_id=str(page.pk)).count() == 1


def test_a_page_already_in_review_is_not_submitted_twice(owner: User) -> None:
    page = _create(owner)
    submit_event(owner, page)
    with pytest.raises(ValidationError):
        submit_event(owner, _fresh(page))


# --- Regression case 3: threshold, policies, staff and platform events


def _approve_events(owner: User, moderator: User, count: int) -> None:
    for n in range(count):
        page = _create(owner, title=f"Approved {n}")
        submit_event(owner, page)
        if _fresh(page).status == EventPage.Status.PENDING:
            _act(page, moderator, "approve")


def test_regression_3_a_higher_threshold_reviews_again(owner: User, moderator: User) -> None:
    _approve_events(owner, moderator, 3)
    staff = UserFactory.create(platform_staff=True)
    site = Site.objects.get(is_default_site=True)
    audits = AuditLog.objects.count()
    set_event_settings(staff, site, moderation_threshold=5)
    assert EventSettings.for_site(site).moderation_threshold == 5
    entry = AuditLog.objects.filter(target_model="events.eventsettings").get()
    assert _json(entry.before)["moderation_threshold"] == 3
    assert _json(entry.after)["moderation_threshold"] == 5
    assert AuditLog.objects.count() == audits + 1

    page = _create(owner, title="Next one")
    submit_event(owner, page)
    assert _fresh(page).status == EventPage.Status.PENDING


def test_regression_3_always_review_and_trusted(moderator: User) -> None:
    staff = UserFactory.create(platform_staff=True)
    reviewed, trusted = _owner(), _owner()
    _approve_events(reviewed, moderator, 3)
    set_vendor_policy(staff, _vendor_of(reviewed), VendorEventPolicy.Mode.ALWAYS_REVIEW)
    set_vendor_policy(staff, _vendor_of(trusted), VendorEventPolicy.Mode.TRUSTED)

    page = _create(reviewed, title="Always")
    submit_event(reviewed, page)
    assert _fresh(page).status == EventPage.Status.PENDING

    page = _create(trusted, title="Trusted")
    submit_event(trusted, page)
    assert _fresh(page).status == EventPage.Status.PUBLISHED


def test_regression_3_staff_and_platform_events_are_never_reviewed() -> None:
    staff = UserFactory.create(platform_staff=True)
    platform = _create(staff, title="Platform event")
    submit_event(staff, platform)
    assert _fresh(platform).status == EventPage.Status.PUBLISHED
    assert _fresh(platform).get_workflow() is None

    vendor_event = EventPageFactory.create()
    vendor_event.save_revision()
    submit_event(staff, vendor_event)
    assert _fresh(vendor_event).status == EventPage.Status.PUBLISHED


def test_get_workflow_follows_the_threshold(owner: User, moderator: User) -> None:
    page = _create(owner)
    workflow = page.get_workflow()
    assert workflow is not None
    assert workflow.name == EVENT_REVIEW
    assert page.has_workflow
    _approve_events(owner, moderator, 3)
    assert _fresh(page).get_workflow() is None


def test_reading_the_threshold_writes_no_settings_row(owner: User) -> None:
    # Wagtail asks for the workflow when it shows a page (a GET): the default threshold applies
    # without creating the site's settings row (SEC.5 "a GET has no side effects").
    page = _create(owner)
    EventSettings.objects.all().delete()
    assert page.get_workflow() is not None
    assert not EventSettings.objects.exists()
    site = Site.objects.get(is_default_site=True)
    assert EventSettings.current(site).moderation_threshold == 3
    assert not EventSettings.objects.exists()


def test_set_vendor_policy_is_audited_and_staff_only(owner: User) -> None:
    staff = UserFactory.create(platform_staff=True)
    vendor = _vendor_of(owner)
    with pytest.raises(PermissionDenied):
        set_vendor_policy(owner, vendor, VendorEventPolicy.Mode.TRUSTED)
    changes = set_vendor_policy(staff, vendor, VendorEventPolicy.Mode.ALWAYS_REVIEW, dry_run=True)
    assert changes.summary()["create"] == 1
    assert not VendorEventPolicy.objects.filter(vendor=vendor).exists()
    set_vendor_policy(staff, vendor, VendorEventPolicy.Mode.ALWAYS_REVIEW)
    set_vendor_policy(staff, vendor, VendorEventPolicy.Mode.TRUSTED)
    assert VendorEventPolicy.objects.get(vendor=vendor).mode == VendorEventPolicy.Mode.TRUSTED
    entries = AuditLog.objects.filter(target_model="events.vendoreventpolicy")
    assert entries.count() == 2
    assert {entry.vendor_key for entry in entries} == {str(vendor.public_id)}


def test_set_event_settings_is_staff_only(owner: User) -> None:
    site = Site.objects.get(is_default_site=True)
    with pytest.raises(PermissionDenied):
        set_event_settings(owner, site, moderation_threshold=0)


# --- Regression case 4: an edit of a live event waits in the workflow


def test_regression_4_an_edit_in_review_keeps_the_approved_revision(
    owner: User, moderator: User
) -> None:
    page = _create(owner)
    submit_event(owner, page)
    _act(page, moderator, "approve")
    approved = _fresh(page).live_revision

    save_event_draft(owner, {"title": "Edited title"}, _fresh(page))
    submit_event(owner, _fresh(page))
    fresh = _fresh(page)
    assert fresh.status == EventPage.Status.PUBLISHED  # a change in review keeps "published"
    assert fresh.workflow_in_progress
    assert fresh.title == "Spring trade fair"
    assert fresh.live_revision == approved

    _act(page, moderator, "approve")
    assert _fresh(page).title == "Edited title"


def test_regression_4_a_vendor_cannot_edit_an_event_awaiting_review(
    owner: User, moderator: User
) -> None:
    page = _create(owner)
    submit_event(owner, page)
    revisions = Revision.objects.count()
    for dry_run in (True, False):
        with pytest.raises(ValidationError):
            save_event_draft(owner, {"title": "UNREVIEWED"}, _fresh(page), dry_run=dry_run)
    assert Revision.objects.count() == revisions

    _act(page, moderator, "approve")
    assert _fresh(page).title == "Spring trade fair"


def test_regression_4_approval_publishes_the_reviewed_revision(
    owner: User, moderator: User
) -> None:
    page = _create(owner)
    submit_event(owner, page)
    # A revision saved past the service (another adapter) while the review is in progress.
    unreviewed = _fresh(page).get_latest_revision_as_object()
    unreviewed.title = "UNREVIEWED"
    unreviewed.save_revision(user=owner)

    _act(page, moderator, "approve")
    assert _fresh(page).title == "Spring trade fair"


def test_regression_4_a_moderator_edit_in_review_is_published_on_approval(
    owner: User, moderator: User
) -> None:
    page = _create(owner)
    submit_event(owner, page)
    save_event_draft(moderator, {"title": "Moderated title"}, _fresh(page))

    _act(page, moderator, "approve")
    assert _fresh(page).title == "Moderated title"


# --- Workflow signal handlers: approval and changes requested are audited


def test_approval_and_changes_requested_are_audited(owner: User, moderator: User) -> None:
    page = _create(owner)
    submit_event(owner, page)
    _act(page, moderator, "reject", comment="Give the booth number.")
    entry = AuditLog.objects.filter(action="events.event_changes_requested").get()
    assert entry.actor_id == moderator.pk
    assert _json(entry.after)["comment"] == "Give the booth number."
    assert entry.vendor_key == _vendor_key(page)

    submit_event(owner, _fresh(page))
    _act(page, moderator, "approve")
    entry = AuditLog.objects.filter(action="events.event_approved").get()
    assert entry.actor_id == moderator.pk
    assert _json(entry.after)["status"] == EventPage.Status.PUBLISHED


def test_submit_writes_one_audit_entry_and_dry_run_none(owner: User) -> None:
    page = _create(owner)
    audits = _audits()
    changes = submit_event(owner, page, dry_run=True)
    assert changes.to_dict()["changes"][0]["fields"]["status"] == ["draft", "pending"]
    assert _fresh(page).status == EventPage.Status.DRAFT
    assert not WorkflowState.objects.filter(object_id=str(page.pk)).exists()
    assert _audits() == audits
    submit_event(owner, page)
    assert _audits() == audits + 1


# --- The sequence rule


def _published(owner: User) -> EventPage:
    services.set_vendor_policy(
        UserFactory.create(platform_staff=True), _vendor_of(owner), VendorEventPolicy.Mode.TRUSTED
    )
    page = _create(owner)
    submit_event(owner, page)
    fresh = _fresh(page)
    assert fresh.live
    return fresh


def test_the_sequence_grows_on_a_date_change(owner: User) -> None:
    page = _published(owner)
    assert page.sequence == 0
    later = STARTS_AT + dt.timedelta(days=7)
    save_event_draft(owner, {"starts_at": later, "ends_at": later + dt.timedelta(hours=8)}, page)
    submit_event(owner, _fresh(page))
    fresh = _fresh(page)
    assert fresh.starts_at == later
    assert fresh.sequence == 1
    assert fresh.content_changed_at is not None


def test_the_sequence_stays_on_a_no_op_publish(owner: User) -> None:
    page = _published(owner)
    save_event_draft(owner, {}, page)
    submit_event(owner, _fresh(page))
    assert _fresh(page).sequence == 0
    _fresh(page).save_revision().publish(skip_permission_checks=True)
    assert _fresh(page).sequence == 0


def test_a_body_only_change_keeps_the_sequence(owner: User) -> None:
    page = _published(owner)
    body = [{"type": "paragraph", "value": "<p>New text.</p>"}]
    save_event_draft(owner, {"body": body}, page)
    submit_event(owner, _fresh(page))
    assert _fresh(page).sequence == 0


# --- Hiding and restoring


def test_a_hide_without_a_reason_is_refused(owner: User) -> None:
    staff = UserFactory.create(platform_staff=True)
    page = _published(owner)
    for reason in ("", "   "):
        with pytest.raises(ValidationError) as excinfo:
            hide_event(staff, page, reason)
        assert "reason" in excinfo.value.message_dict
    assert _fresh(page).live


def test_only_staff_hide_events(owner: User) -> None:
    page = _published(owner)
    with pytest.raises(PermissionDenied):
        hide_event(owner, page, "Mine")


def test_hiding_unpublishes_and_records_the_reason(owner: User) -> None:
    staff = UserFactory.create(platform_staff=True)
    page = _published(owner)
    audits = _audits()
    hide_event(staff, page, "Misleading offer", dsa_notice_ref="N-1")
    fresh = _fresh(page)
    assert not fresh.live
    assert fresh.status == EventPage.Status.HIDDEN
    assert fresh.hidden_at is not None
    assert fresh.hidden_by == staff
    assert fresh.hidden_reason == "Misleading offer"
    assert fresh.dsa_notice_ref == "N-1"
    assert _audits() == audits + 1


def test_a_hide_dry_run_changes_nothing(owner: User) -> None:
    staff = UserFactory.create(platform_staff=True)
    page = _published(owner)
    audits = _audits()
    changes = hide_event(staff, page, "Misleading offer", dry_run=True)
    assert not changes.is_empty
    assert _fresh(page).live
    assert _fresh(page).hidden_at is None
    assert _audits() == audits


def test_a_hidden_page_cannot_be_resubmitted_by_the_vendor(owner: User) -> None:
    staff = UserFactory.create(platform_staff=True)
    page = _published(owner)
    hide_event(staff, page, "Misleading offer")
    with pytest.raises(EventRefused):
        submit_event(owner, _fresh(page))
    with pytest.raises(EventRefused):
        save_event_draft(owner, {"title": "Try again"}, _fresh(page))
    assert not _fresh(page).live


def test_a_vendor_edit_does_not_clear_the_hiding(owner: User) -> None:
    """Hiding lives on the page row: no revision carries it back or clears it."""
    staff = UserFactory.create(platform_staff=True)
    page = _published(owner)
    hide_event(staff, page, "Misleading offer")
    hidden = _fresh(page)
    restored = hidden.get_latest_revision_as_object()
    assert restored.hidden_at == hidden.hidden_at


def test_restore_republishes_the_approved_revision(owner: User) -> None:
    staff = UserFactory.create(platform_staff=True)
    page = _published(owner)
    approved = page.live_revision
    hide_event(staff, page, "Misleading offer")
    audits = _audits()
    restore_event(staff, _fresh(page))
    fresh = _fresh(page)
    assert fresh.live
    assert fresh.status == EventPage.Status.PUBLISHED
    assert fresh.hidden_at is None
    assert fresh.hidden_reason == ""
    assert fresh.live_revision == approved
    assert fresh.sequence == 1
    assert _audits() == audits + 1


def test_only_staff_restore_and_only_hidden_events(owner: User) -> None:
    staff = UserFactory.create(platform_staff=True)
    page = _published(owner)
    with pytest.raises(ValidationError):
        restore_event(staff, page)
    hide_event(staff, page, "Misleading offer")
    with pytest.raises(PermissionDenied):
        restore_event(owner, _fresh(page))


# --- Cancelling


def test_cancelling_publishes_the_live_revision_with_a_new_sequence(
    owner: User, moderator: User
) -> None:
    page = _create(owner)
    submit_event(owner, page)
    _act(page, moderator, "approve")
    save_event_draft(owner, {"title": "Pending edit"}, _fresh(page))
    submit_event(owner, _fresh(page))  # an edit waits in review
    audits = _audits()

    cancel_event(owner, _fresh(page))
    fresh = _fresh(page)
    assert fresh.live
    assert fresh.status == EventPage.Status.CANCELLED
    assert fresh.cancelled_at is not None
    assert fresh.sequence == 1
    assert fresh.title == "Spring trade fair"  # the live revision, not the pending edit
    assert not fresh.workflow_in_progress
    assert _audits() == audits + 1


def test_only_a_live_event_can_be_cancelled(owner: User) -> None:
    page = _create(owner)
    with pytest.raises(ValidationError):
        cancel_event(owner, page)


def test_a_cancel_dry_run_changes_nothing(owner: User) -> None:
    page = _published(owner)
    revisions = Revision.objects.count()
    changes = cancel_event(owner, page, dry_run=True)
    assert changes.to_dict()["changes"][0]["fields"]["status"] == ["published", "cancelled"]
    assert Revision.objects.count() == revisions
    assert _fresh(page).cancelled_at is None


def test_a_vendor_cannot_cancel_another_vendors_event(owner: User) -> None:
    other = _published(_owner())
    with pytest.raises(Http404):
        cancel_event(owner, other)


# --- CORE.8: `is_staff` (Wagtail admin login) alone is not platform staff


def test_an_admin_login_without_the_permission_cannot_hide_events(owner: User) -> None:
    editor = UserFactory.create(staff=True)
    page = _published(owner)
    with pytest.raises(PermissionDenied):
        hide_event(editor, page, "Not allowed")
    assert _fresh(page).live


def test_an_admin_login_without_the_permission_cannot_set_a_vendor_policy(owner: User) -> None:
    editor = UserFactory.create(staff=True)
    with pytest.raises(PermissionDenied):
        set_vendor_policy(editor, _vendor_of(owner), VendorEventPolicy.Mode.TRUSTED)
    assert not VendorEventPolicy.objects.filter(vendor=_vendor_of(owner)).exists()


def test_an_admin_login_without_the_permission_cannot_edit_another_vendors_event() -> None:
    editor = UserFactory.create(staff=True)
    theirs = EventPageFactory.create()
    with pytest.raises((Http404, PermissionDenied)):
        save_event_draft(editor, {"title": "Mine now"}, theirs)


def test_moderation_rights_need_the_permission() -> None:
    from apps.events.services import may_moderate_events

    assert not may_moderate_events(UserFactory.create(staff=True))
    assert may_moderate_events(UserFactory.create(platform_staff=True))


def test_a_vendor_members_staff_flag_does_not_open_the_admin_or_other_vendors(
    owner: User,
) -> None:
    from apps.events.services import may_create_events_in_admin

    owner.is_staff = True
    owner.save(update_fields=["is_staff"])
    assert not may_create_events_in_admin(owner)
    theirs = EventPageFactory.create()
    with pytest.raises(Http404):
        save_event_draft(owner, {"title": "Mine now"}, theirs)
