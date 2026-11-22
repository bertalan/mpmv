"""EVT.8: the vendor area writes events in any language and adds translations (spec 15
"Languages", "Vendor area"): a language choice on create, "Add translation" through
`services.add_event_translation`, a text-only form for a translation, and the languages in the
list. Another shop's event is a 404 on the new view too."""

import datetime as dt
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from wagtail.models import Locale

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms.services import seed_site
from apps.core.i18n import configured_languages
from apps.core.models import AuditLog
from apps.events import services
from apps.events.models import EventPage, PolicyMode, VendorEventPolicy
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db

LIST = "manager:shop:events"
NEW = "manager:shop:event_new"


@pytest.fixture(autouse=True)
def _site() -> None:
    seed_site()


@pytest.fixture
def vendor() -> Vendor:
    return VendorFactory.create(display_name="Shop One", status=Vendor.Status.ACTIVE)


@pytest.fixture
def owner(vendor: Vendor) -> User:
    user: User = VendorMemberFactory.create(vendor=vendor).user
    return user


@pytest.fixture
def signed_in(client: Client, owner: User) -> Client:
    verified_login(client, owner)
    return client


def _post(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Herbstmesse",
        "summary": "Treffen Sie die Hersteller.",
        "event_type": "fair",
        "starts_at": "2027-10-02T09:00",
        "ends_at": "2027-10-02T18:00",
        "timezone": "Europe/Rome",
        "recurrence": "none",
        "attendance": "in_person",
        "venue_name": "Messe",
        "city": "Bolzano",
        "country": "IT",
    }
    data.update(changes)
    return data


def _german_event(owner: User, vendor: Vendor, *, publish: bool = True) -> EventPage:
    VendorEventPolicy.objects.get_or_create(vendor=vendor, defaults={"mode": PolicyMode.TRUSTED})
    page = services.save_event_draft(
        owner,
        {
            "title": "Herbstmesse",
            "summary": "Treffen Sie die Hersteller.",
            "event_type": "fair",
            "starts_at": dt.datetime(2027, 10, 2, 7, tzinfo=dt.UTC),
            "ends_at": dt.datetime(2027, 10, 2, 16, tzinfo=dt.UTC),
            "timezone": "Europe/Rome",
            "attendance": "in_person",
            "city": "Bolzano",
            "country": "IT",
        },
        vendor=vendor,
        locale=Locale.objects.get(language_code="de"),
    ).page
    assert page is not None
    if publish:
        services.submit_event(owner, page)
    found: EventPage = EventPage.objects.get(pk=page.pk)
    return found


def _translate_url(page: EventPage) -> str:
    return reverse("manager:shop:event_translate", args=[page.public_id])


def test_the_new_form_offers_every_enabled_language(signed_in: Client) -> None:
    form = signed_in.get(reverse(NEW)).context["form"]
    assert [code for code, _name in form.fields["language"].choices] == [
        "en",
        "it",
        "de",
        "fr",
        "es",
    ]


def test_a_new_event_is_written_in_the_chosen_language(signed_in: Client) -> None:
    response = signed_in.post(reverse(NEW), _post(language="de"))
    assert response.status_code == 302, response.context["form"].errors
    page = EventPage.objects.get(title="Herbstmesse")
    assert page.locale.language_code == "de"
    assert page.original_locale.language_code == "de"


def test_an_unknown_language_is_a_field_error(signed_in: Client) -> None:
    response = signed_in.post(reverse(NEW), _post(language="xx"))
    assert response.status_code == 200
    assert "language" in response.context["form"].errors
    assert not EventPage.objects.filter(title="Herbstmesse").exists()


def test_the_edit_form_shows_the_original_language_and_cannot_change_it(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    page = _german_event(owner, vendor)
    edit = reverse("manager:shop:event_edit", args=[page.public_id])
    response = signed_in.get(edit)
    assert "language" not in response.context["form"].fields
    assert "Deutsch" in response.text
    assert signed_in.post(edit, _post(title="Herbstmesse 2", language="it")).status_code == 302
    assert EventPage.objects.get(pk=page.pk).locale.language_code == "de"


def test_the_list_offers_add_translation_and_shows_the_languages(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    page = _german_event(owner, vendor)
    html = signed_in.get(reverse(LIST)).text
    assert _translate_url(page) in html
    assert "Add translation" in html
    assert "German" in html or "Deutsch" in html
    services.add_event_translation(
        owner, page, Locale.objects.get(language_code="it"), {"title": "Fiera", "summary": "x"}
    )
    html = signed_in.get(reverse(LIST)).text
    assert "Fiera" in html
    translation = EventPage.objects.get(title="Fiera")
    assert _translate_url(translation) not in html  # a translation is translated from the original


def test_add_translation_creates_the_draft_and_opens_its_text_form(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    page = _german_event(owner, vendor)
    form = signed_in.get(_translate_url(page))
    assert form.status_code == 200
    choices = [code for code, _name in form.context["form"].fields["language"].choices]
    assert choices == ["en", "it", "fr", "es"]  # the original language is not a choice
    response = signed_in.post(
        _translate_url(page),
        {"language": "it", "title": "Fiera d'autunno", "summary": "Incontra gli artigiani."},
    )
    assert response.status_code == 302
    translation = EventPage.objects.get(
        locale__language_code="it", translation_key=page.translation_key
    )
    assert translation.status == EventPage.Status.DRAFT
    assert response["Location"] == reverse("manager:shop:event_edit", args=[translation.public_id])
    assert translation.get_latest_revision_as_object().title == "Fiera d'autunno"


def test_the_translation_form_has_the_text_fields_only(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    page = _german_event(owner, vendor)
    translation = services.add_event_translation(
        owner, page, Locale.objects.get(language_code="it"), {"title": "Fiera", "summary": "x"}
    ).page
    assert translation is not None
    edit = reverse("manager:shop:event_edit", args=[translation.public_id])
    html = signed_in.get(edit).text
    for name in ("title", "summary", "venue_name"):
        assert f'name="{name}"' in html
    for name in ("starts_at", "recurrence", "city", "latitude"):
        assert f'name="{name}"' not in html
    assert (
        signed_in.post(
            edit, {"title": "Fiera nuova", "summary": "y", "venue_name": "Sala"}
        ).status_code
        == 302
    )
    assert (
        EventPage.objects.get(pk=translation.pk).get_latest_revision_as_object().title
        == "Fiera nuova"
    )
    submit = reverse("manager:shop:event_submit", args=[translation.public_id])
    assert signed_in.post(submit).status_code == 302
    published = EventPage.objects.get(pk=translation.pk)
    assert published.live
    assert published.city == "Bolzano"  # the structured fields are the original's


def test_adding_a_language_twice_is_a_field_error(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    page = _german_event(owner, vendor)
    data = {"language": "it", "title": "Fiera", "summary": "x"}
    assert signed_in.post(_translate_url(page), data).status_code == 302
    again = signed_in.post(_translate_url(page), data)
    assert again.status_code == 200
    assert "language" in again.context["form"].errors


def test_add_translation_of_another_shops_event_is_a_404(client: Client, owner: User) -> None:
    other = VendorFactory.create(status=Vendor.Status.ACTIVE)
    theirs = _german_event(VendorMemberFactory.create(vendor=other).user, other)
    verified_login(client, owner)
    assert client.get(_translate_url(theirs)).status_code == 404
    assert (
        client.post(
            _translate_url(theirs), {"language": "it", "title": "T", "summary": "S"}
        ).status_code
        == 404
    )
    assert not EventPage.objects.filter(locale__language_code="it").exists()


def test_a_customer_and_an_anonymous_visitor_cannot_add_translations(
    client: Client, owner: User, vendor: Vendor
) -> None:
    page = _german_event(owner, vendor)
    assert client.get(_translate_url(page)).status_code in (302, 403)
    verified_login(client, UserFactory.create())
    assert client.get(_translate_url(page)).status_code in (302, 403, 404)


def test_the_translate_pages_render_under_the_ride_theme(
    signed_in: Client, owner: User, vendor: Vendor, settings: Any
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    page = _german_event(owner, vendor)
    response = signed_in.get(_translate_url(page))
    assert response.status_code == 200
    assert 'data-theme="ride"' in response.text


def test_a_suspended_vendor_cannot_add_a_translation(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    """D-44 / Q-53: the vendor is suspended after the event exists; no row is written."""
    page = _german_event(owner, vendor)
    target = next(code for code in configured_languages() if code != "de")
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    response = signed_in.post(
        _translate_url(page), {"language": target, "title": "T", "summary": "S"}
    )
    # The form is shown again with a refusal; it is not a redirect to the saved draft.
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert not EventPage.objects.filter(locale__language_code=target).exists()
    assert not AuditLog.objects.filter(action="events.event_translation_added").exists()
