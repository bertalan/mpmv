"""EVT.4 part 1: the vendor's events in the Manager shop space (spec 15 "Vendor area").

List scoped to the selected shop, the structured form with recurrence presets, submit and
cancel through the EVT.3 services, and a negative tenant test on every view.
"""

import datetime as dt
from typing import Any
from uuid import uuid4

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse
from wagtail.models import WorkflowState

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.events import services
from apps.events.models import EVENT_MODERATORS, EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _index() -> None:
    primary_index()


def _post_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Autumn fair",
        "summary": "Meet the makers at the autumn fair.",
        "event_type": "fair",
        "starts_at": "2027-10-02T09:00",
        "ends_at": "2027-10-02T18:00",
        "timezone": "Europe/Rome",
        "recurrence": "none",
        "attendance": "in_person",
        "venue_name": "Exhibition centre",
        "city": "Milano",
        "country": "IT",
    }
    data.update(changes)
    return data


def _event(vendor: Vendor, **fields: Any) -> EventPage:
    page: EventPage = EventPageFactory.create(vendor=vendor, **fields)
    page.save_revision()
    return page


def _fresh(page: EventPage) -> EventPage:
    found: EventPage = EventPage.objects.get(pk=page.pk)
    return found


def _publish(page: EventPage) -> EventPage:
    page.get_latest_revision().publish()
    return _fresh(page)


@pytest.fixture
def vendor(db: None) -> Vendor:
    """An active shop: the services refuse writes for a vendor that is not active yet."""
    return VendorFactory.create(display_name="Shop One", status=Vendor.Status.ACTIVE)


@pytest.fixture
def signed_in(client: Client, owner: User) -> Client:
    verified_login(client, owner)
    return client


def _urls(page: EventPage) -> dict[str, str]:
    args = [page.public_id]
    return {
        "edit": reverse("manager:shop:event_edit", args=args),
        "submit": reverse("manager:shop:event_submit", args=args),
        "cancel": reverse("manager:shop:event_cancel", args=args),
    }


LIST = "manager:shop:events"
NEW = "manager:shop:event_new"


# --- List


def test_the_list_shows_the_shops_events_with_status_and_dates(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor, title="Our spring sale")
    response = signed_in.get(reverse(LIST))
    assert response.status_code == 200
    html = response.text
    assert "Our spring sale" in html
    assert "Draft" in html
    assert _urls(page)["edit"] in html
    assert reverse(NEW) in html


def test_the_list_never_shows_another_shops_or_a_platform_event(
    signed_in: Client, other_vendor: Vendor
) -> None:
    _event(other_vendor, title="Rival fair")
    EventPageFactory.create(platform=True, title="Platform fair")
    html = signed_in.get(reverse(LIST)).text
    assert "Rival fair" not in html
    assert "Platform fair" not in html


def test_the_list_shows_the_moderators_comment_when_changes_are_requested(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    page = _event(vendor, title="Needs work")
    services.submit_event(owner, page)
    moderator = UserFactory.create(superuser=True)
    moderator.groups.add(Group.objects.get(name=EVENT_MODERATORS))
    state = _fresh(page).current_workflow_state
    assert isinstance(state, WorkflowState)
    task_state = state.current_task_state
    task_state.task.specific.on_action(task_state, moderator, "reject", comment="Give the booth.")
    html = signed_in.get(reverse(LIST)).text
    assert "Give the booth." in html
    assert "Changes requested" in html


def test_the_list_shows_the_hide_reason(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor, title="Hidden one")
    EventPage.objects.filter(pk=page.pk).update(
        hidden_reason="Misleading offer.", hidden_at=dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
    )
    assert "Misleading offer." in signed_in.get(reverse(LIST)).text


# --- Create and edit


def test_creating_an_event_saves_a_draft_of_the_shop(signed_in: Client, vendor: Vendor) -> None:
    assert signed_in.get(reverse(NEW)).status_code == 200
    response = signed_in.post(reverse(NEW), _post_data())
    assert response.status_code == 302
    page = EventPage.objects.get(title="Autumn fair")
    assert page.vendor == vendor
    assert page.status == EventPage.Status.DRAFT
    assert page.starts_at == dt.datetime(2027, 10, 2, 7, 0, tzinfo=dt.UTC)  # Rome is UTC+2
    assert response["Location"] == reverse("manager:shop:event_edit", args=[page.public_id])
    assert AuditLog.objects.filter(action="events.event_created").count() == 1


def test_a_new_event_is_written_in_the_active_language(signed_in: Client) -> None:
    services.ensure_event_indexes()
    User.objects.update(preferred_language="it")  # I18N.12: decides the Manager's language
    response = signed_in.post(reverse(NEW), _post_data())
    assert response.status_code == 302
    page = EventPage.objects.get(title="Autumn fair")
    assert page.locale.language_code == "it"
    assert page.original_locale.language_code == "it"


def test_the_weekly_preset_writes_the_rrule_and_the_form_shows_it_again(
    signed_in: Client,
) -> None:
    data = _post_data(
        recurrence="weekly", recurrence_weekdays=["WE", "MO"], recurrence_interval="1"
    )
    signed_in.post(reverse(NEW), data)
    page = EventPage.objects.get(title="Autumn fair")
    assert page.rrule == "FREQ=WEEKLY;BYDAY=MO,WE"
    html = signed_in.get(_urls(page)["edit"]).text
    assert 'value="weekly" selected' in html or 'value="weekly" checked' in html


def test_a_monthly_preset_with_a_last_day_and_a_count(signed_in: Client) -> None:
    signed_in.post(
        reverse(NEW),
        _post_data(
            recurrence="monthly",
            recurrence_month_day="-1",
            recurrence_ends="count",
            recurrence_count="6",
        ),
    )
    assert EventPage.objects.get(title="Autumn fair").rrule == "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=6"


def test_a_preset_with_missing_input_is_a_field_error_and_saves_nothing(
    signed_in: Client,
) -> None:
    response = signed_in.post(reverse(NEW), _post_data(recurrence="weekly"))
    assert response.status_code == 200
    assert "Choose at least one weekday" in response.text
    assert not EventPage.objects.filter(title="Autumn fair").exists()


def test_an_all_day_event_takes_days_and_a_promotion_has_no_place(signed_in: Client) -> None:
    signed_in.post(
        reverse(NEW),
        _post_data(
            title="Big sale",
            event_type="promotion",
            all_day="on",
            starts_at="",
            ends_at="",
            start_date="2027-11-26",
            end_date="2027-11-29",
            attendance="in_person",
            city="Milano",
            venue_name="Somewhere",
        ),
    )
    page = EventPage.objects.get(title="Big sale")
    assert (page.start_date, page.end_date) == (dt.date(2027, 11, 26), dt.date(2027, 11, 29))
    assert (page.attendance, page.city, page.venue_name) == ("", "", "")


def test_invalid_data_shows_errors_and_keeps_the_input(signed_in: Client) -> None:
    response = signed_in.post(
        reverse(NEW), _post_data(ends_at="2027-10-02T08:00", summary="Keep me")
    )
    assert response.status_code == 200
    assert "The event must end after it starts." in response.text
    assert "Keep me" in response.text
    assert not EventPage.objects.filter(title="Autumn fair").exists()


def test_editing_saves_a_new_revision_and_prefills_the_form(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor, title="Old title")
    html = signed_in.get(_urls(page)["edit"]).text
    assert "Old title" in html
    response = signed_in.post(_urls(page)["edit"], _post_data(title="New title"))
    assert response.status_code == 302
    latest = _fresh(page).get_latest_revision_as_object()
    assert latest.title == "New title"
    assert AuditLog.objects.filter(action="events.event_draft_saved").exists()


def test_a_page_awaiting_review_cannot_be_edited(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    page = _event(vendor)
    services.submit_event(owner, page)
    response = signed_in.post(_urls(page)["edit"], _post_data(title="Sneaky"))
    assert response.status_code == 200
    assert "awaiting review" in response.text
    assert _fresh(page).get_latest_revision_as_object().title != "Sneaky"


# --- Submit and cancel


def test_submitting_starts_the_review_and_audits_it(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    response = signed_in.post(_urls(page)["submit"])
    assert response.status_code == 302
    assert response["Location"] == reverse(LIST)
    assert _fresh(page).status == EventPage.Status.PENDING
    assert AuditLog.objects.filter(action="events.event_submitted").count() == 1


def test_submitting_twice_is_refused_with_a_message(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    signed_in.post(_urls(page)["submit"])
    response = signed_in.post(_urls(page)["submit"], follow=True)
    assert "already awaiting review" in response.text
    assert AuditLog.objects.filter(action="events.event_submitted").count() == 1


def test_cancel_asks_first_and_then_cancels_a_published_event(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _publish(_event(vendor))
    confirm = signed_in.get(_urls(page)["cancel"])
    assert confirm.status_code == 200
    assert _fresh(page).cancelled_at is None  # a GET changes nothing
    response = signed_in.post(_urls(page)["cancel"])
    assert response.status_code == 302
    assert _fresh(page).cancelled_at is not None
    assert AuditLog.objects.filter(action="events.event_cancelled").count() == 1


def test_a_draft_cannot_be_cancelled(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    response = signed_in.post(_urls(page)["cancel"], follow=True)
    assert "Only a published event can be cancelled." in response.text
    assert _fresh(page).cancelled_at is None


# --- Negative tenant tests on every view


def _every_view(page: EventPage) -> list[tuple[str, str]]:
    urls = _urls(page)
    return [
        ("get", reverse(LIST)),
        ("get", reverse(NEW)),
        ("post", reverse(NEW)),
        ("get", urls["edit"]),
        ("post", urls["edit"]),
        ("post", urls["submit"]),
        ("get", urls["cancel"]),
        ("post", urls["cancel"]),
    ]


def test_another_shops_event_is_a_404_on_every_view_that_takes_an_id(
    client: Client, other_vendor: Vendor, owner: User
) -> None:
    theirs = _publish(_event(other_vendor, title="Theirs"))
    verified_login(client, owner)
    for method, url in _every_view(theirs)[3:]:
        response = getattr(client, method)(url, _post_data(title="Taken over"))
        assert response.status_code == 404, (method, url)
    unchanged = _fresh(theirs)
    assert unchanged.cancelled_at is None
    assert unchanged.get_latest_revision_as_object().title == "Theirs"
    assert unchanged.status == EventPage.Status.PUBLISHED


def test_a_random_id_is_a_404(signed_in: Client) -> None:
    for name in ("event_edit", "event_submit", "event_cancel"):
        assert signed_in.get(reverse(f"manager:shop:{name}", args=[uuid4()])).status_code in {
            404,
            405,
        }


def test_a_member_of_another_shop_sees_no_event_and_changes_none(
    client: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    page = _publish(_event(vendor, title="Ours"))
    stranger = VendorMemberFactory.create(vendor=other_vendor, role="owner").user
    verified_login(client, stranger)
    assert "Ours" not in client.get(reverse(LIST)).text
    for method, url in _every_view(page)[3:]:
        assert getattr(client, method)(url, _post_data()).status_code == 404, (method, url)
    assert _fresh(page).cancelled_at is None


def test_a_customer_gets_a_403_on_every_view(
    client: Client, customer: User, vendor: Vendor
) -> None:
    page = _event(vendor)
    verified_login(client, customer)
    for method, url in _every_view(page):
        assert getattr(client, method)(url, _post_data()).status_code == 403, (method, url)
    assert EventPage.objects.filter(vendor=vendor).count() == 1


def test_an_anonymous_visitor_is_sent_to_the_login_on_every_view(
    client: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    for method, url in _every_view(page):
        response = getattr(client, method)(url, _post_data())
        assert response.status_code == 302, (method, url)
        assert "/account/login/" in response["Location"]
    assert EventPage.objects.filter(vendor=vendor).count() == 1


def test_vendor_staff_may_manage_events_too(client: Client, vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, staff)
    assert client.post(reverse(NEW), _post_data()).status_code == 302
    assert EventPage.objects.filter(vendor=vendor, title="Autumn fair").exists()


def test_the_events_page_is_in_the_shop_menu(signed_in: Client) -> None:
    html = signed_in.get(reverse("manager:shop:dashboard")).text
    assert reverse(LIST) in html


def test_a_shop_that_is_not_active_yet_gets_a_message_not_an_error(
    client: Client, other_vendor: Vendor
) -> None:
    applicant = VendorFactory.create(status=Vendor.Status.APPLICANT)
    member = VendorMemberFactory.create(vendor=applicant, role="owner").user
    verified_login(client, member)
    response = client.post(reverse(NEW), _post_data())
    assert response.status_code == 200
    assert "not active yet" in response.text
    assert not EventPage.objects.filter(vendor=applicant).exists()


def test_the_pages_render_under_the_ride_theme(
    signed_in: Client, vendor: Vendor, settings: Any
) -> None:
    # The site default theme, not apps.themes: the "No app imports themes" contract.
    settings.MPMV_THEME_DEFAULT = "ride"
    page = _publish(_event(vendor, title="Ride fair"))
    for url in (reverse(LIST), reverse(NEW), _urls(page)["edit"], _urls(page)["cancel"]):
        response = signed_in.get(url)
        assert response.status_code == 200, url
        assert 'data-theme="ride"' in response.text, url
    html = signed_in.get(_urls(page)["edit"]).text
    assert 'name="recurrence"' in html
    assert 'name="title"' in html
