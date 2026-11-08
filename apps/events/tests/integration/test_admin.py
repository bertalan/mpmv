"""EVT.1 part 2/2: the Wagtail admin panels of event pages (spec 15 "Staff admin").

Staff edit any event in the Wagtail admin; vendors never do (spec 15 "How vendors author an
`EventPage`"): a vendor member without admin access is sent to the login, and one who was given
Wagtail access anyway (an editor group) is refused on event pages. The geocoding metadata and the
moderation state are read-only (services write them); the moderation panel is shown to staff
only. The product chooser offers only the event vendor's products.
"""

import datetime as dt
import secrets
from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog.tests.factories import ProductFactory
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.tests.factories import VendorMemberFactory

pytestmark = pytest.mark.django_db

GEOCODED_AT = dt.datetime(2027, 1, 5, 10, 0, tzinfo=dt.UTC)


def _staff(client: Client) -> User:
    user: User = UserFactory.create(superuser=True)
    verified_login(client, user)
    return user


def _editor() -> User:
    """A non-staff member of Wagtail's "Editors" group: admin access, no staff status."""
    user: User = UserFactory.create(password=secrets.token_urlsafe(16))
    user.groups.add(Group.objects.get(name="Editors"))
    return user


def _geocoded_event(**kwargs: Any) -> EventPage:
    page: EventPage = EventPageFactory.create(
        located=True,
        geo_source=EventPage.GeoSource.GEOCODED,
        geo_provider="fakeprovider",
        geo_precision="street",
        geo_confidence=0.87,
        geo_at=GEOCODED_AT,
        hidden_reason="Reported listing under review",
        **kwargs,
    )
    return page


def _form(page: EventPage, user: User, data: dict[str, Any] | None = None) -> Any:
    form_class = EventPage.get_edit_handler().get_form_class()
    return form_class(data, instance=page, parent_page=page.get_parent(), for_user=user)


def _edit_url(page: EventPage) -> str:
    return reverse("wagtailadmin_pages:edit", args=(page.pk,))


# --- Staff


def test_staff_edit_view_groups_the_fields_per_spec(client: Client) -> None:
    _staff(client)
    page = _geocoded_event()
    response = client.get(_edit_url(page))
    assert response.status_code == 200
    html = response.content.decode()
    for heading in ("When", "Where", "Attendance", "Links", "Moderation"):
        assert heading in html, heading
    for name in ("summary", "event_type", "starts_at", "ends_at", "timezone", "rrule"):
        assert f'name="{name}"' in html, name
    for name in ("attendance", "venue_name", "city", "country", "latitude", "longitude"):
        assert f'name="{name}"' in html, name
    for name in ("products", "categories", "listing_image"):
        assert f'name="{name}"' in html, name


def test_geocoding_metadata_is_shown_read_only(client: Client) -> None:
    _staff(client)
    page = _geocoded_event()
    html = client.get(_edit_url(page)).content.decode()
    assert "fakeprovider" in html
    assert "street" in html
    for name in ("geo_provider", "geo_precision", "geo_confidence", "geo_at"):
        assert f'name="{name}"' not in html, name


def test_the_form_cannot_write_geocoding_metadata_or_moderation_state() -> None:
    staff = UserFactory.create(superuser=True)
    fields = set(_form(_geocoded_event(), staff).fields)
    assert {"latitude", "longitude", "geo_source"} <= fields
    forbidden = {
        "geo_provider",
        "geo_precision",
        "geo_confidence",
        "geo_at",
        "status",
        "sequence",
        "cancelled_at",
        "hidden_at",
        "hidden_by",
        "hidden_reason",
        "dsa_notice_ref",
        "content_changed_at",
        "original_locale",
        "public_id",
    }
    assert not fields & forbidden


def test_the_moderation_state_is_shown_to_staff(client: Client) -> None:
    _staff(client)
    html = client.get(_edit_url(_geocoded_event())).content.decode()
    assert "Reported listing under review" in html


def test_staff_create_view_offers_the_event_form(client: Client) -> None:
    _staff(client)
    index = primary_index()
    url = reverse("wagtailadmin_pages:add", args=("events", "eventpage", index.pk))
    response = client.get(url)
    assert response.status_code == 200
    assert 'name="starts_at"' in response.content.decode()


def test_staff_create_an_event_through_the_admin(client: Client) -> None:
    _staff(client)
    index = primary_index()
    url = reverse("wagtailadmin_pages:add", args=("events", "eventpage", index.pk))
    response = client.post(
        url,
        {
            "title": "Spring fair",
            "slug": "spring-fair",
            "summary": "Makers at the spring fair.",
            "event_type": EventPage.EventType.FAIR,
            "starts_at": "2027-03-10 09:00",
            "ends_at": "2027-03-10 17:00",
            "timezone": "Europe/Rome",
            "attendance": EventPage.Attendance.IN_PERSON,
            "venue_name": "Exhibition centre",
            "city": "Milano",
            "country": "IT",
            "body-count": "0",
            "exdates": "",
            "exdate_days": "",
        },
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    page = EventPage.objects.get(slug="spring-fair")
    assert page.original_locale_id == page.locale_id == index.locale_id
    # Times are typed in the event's time zone: 09:00 in Rome is 08:00 UTC in March.
    assert page.starts_at == dt.datetime(2027, 3, 10, 8, 0, tzinfo=dt.UTC)
    assert page.vendor is None  # a platform event


# --- Vendor-scoped product chooser


def test_the_product_chooser_offers_only_the_event_vendors_products() -> None:
    staff = UserFactory.create(superuser=True)
    page = EventPageFactory.create()
    own = ProductFactory.create(vendor=page.vendor)
    other = ProductFactory.create()
    choices = set(_form(page, staff).fields["products"].queryset)
    assert own in choices
    assert other not in choices


def test_a_crafted_post_cannot_link_another_vendors_product() -> None:
    staff = UserFactory.create(superuser=True)
    page = EventPageFactory.create()
    other = ProductFactory.create()
    form = _form(page, staff, {"title": page.title, "products": [str(other.pk)], "body-count": "0"})
    assert not form.is_valid()
    assert "products" in form.errors


def test_the_vendor_of_an_existing_event_cannot_change() -> None:
    staff = UserFactory.create(superuser=True)
    form = _form(EventPageFactory.create(), staff)
    assert form.fields["vendor"].disabled


# --- Non-staff editors and vendor members


def test_a_non_staff_editor_does_not_see_the_moderation_panel(client: Client) -> None:
    editor = _editor()
    verified_login(client, editor)
    page = _geocoded_event(platform=True)
    response = client.get(_edit_url(page))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Reported listing under review" not in html
    assert "vendor" not in _form(page, editor).fields


def test_a_vendor_member_cannot_open_the_admin(client: Client) -> None:
    member = VendorMemberFactory.create()
    page = EventPageFactory.create(vendor=member.vendor)
    verified_login(client, member.user)  # vendor owners verify a second factor too
    response = client.get(_edit_url(page))
    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_a_vendor_member_with_wagtail_access_is_refused_on_event_pages(client: Client) -> None:
    member = VendorMemberFactory.create()
    member.user.groups.add(Group.objects.get(name="Editors"))
    verified_login(client, member.user)
    own = EventPageFactory.create(vendor=member.vendor)
    index = primary_index()
    # Wagtail turns the refusal into a redirect to the admin home with an error message.
    refused = reverse("wagtailadmin_home")
    for url in (
        _edit_url(own),
        reverse("wagtailadmin_pages:add", args=("events", "eventpage", index.pk)),
        reverse("wagtailadmin_pages:delete", args=(own.pk,)),
    ):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response["Location"] == refused, url
