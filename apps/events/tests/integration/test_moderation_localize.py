"""EVT.3 part 2/2 review fix: hiding holds on every publish path, wagtail-localize's included
(spec 15 "Hiding": a hidden page "cannot be submitted or published again ... only staff restore
it"; "Moderation with Wagtail workflows": a non-staff editor never publishes without review).

wagtail-localize's "Update translations", "Translate" and string editor views run no
`before_edit_page` hook, so the guard is twofold: `EventPage.publish` refuses a hidden page
unless `restore_event` publishes it, and the localize views refuse a non-staff user on a hidden
event, or a publishing request while the vendor needs review.
"""

import datetime as dt
import secrets
from typing import Any

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from wagtail.models import GroupPagePermission, Locale, Page
from wagtail_localize.models import Translation, TranslationSource

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.cms import roles
from apps.cms.services import translate_page
from apps.events.models import EventPage, PolicyMode, VendorEventPolicy
from apps.events.services import hide_event, restore_event, save_event_draft, submit_event
from apps.events.tests.factories import STARTS_AT
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """wagtail-localize publishes a translation when the transaction commits; a test runs in
    one transaction that never commits, so the hooks run at once here."""
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _fresh(page: EventPage) -> EventPage:
    fresh: EventPage = EventPage.objects.get(pk=page.pk)
    return fresh


def _live_translated() -> tuple[EventPage, EventPage]:
    """A live event of a trusted vendor and its live EN translation."""
    vendor = VendorFactory.create(status=Vendor.Status.ACTIVE)
    owner = VendorMemberFactory.create(vendor=vendor).user
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
    page = save_event_draft(owner, data, vendor=vendor).page
    assert page is not None
    VendorEventPolicy.objects.create(vendor=vendor, mode=PolicyMode.TRUSTED)
    submit_event(owner, page)
    page = _fresh(page)
    assert page.live
    translate_page(page, ["en"])
    english = _fresh(page.get_translation(Locale.objects.get(language_code="en")).specific)
    assert english.live
    return page, english


def _hidden_translated() -> tuple[EventPage, EventPage]:
    page, english = _live_translated()
    hide_event(UserFactory.create(superuser=True), page, "Misleading offer.")
    page, english = _fresh(page), _fresh(english)
    assert not page.live
    assert not english.live
    return page, english


def _editor() -> User:
    """A plain member of the "Page editors" role: wagtail_localize.submit_translation, no
    staff status, no publish permission."""
    user: User = UserFactory.create(password=secrets.token_urlsafe(16))
    user.groups.add(Group.objects.get(name=roles.PAGE_EDITORS))
    assert user.has_perm("wagtail_localize.submit_translation")
    return user


def _source(page: EventPage) -> TranslationSource:
    source, _created = TranslationSource.get_or_create_from_instance(page)
    return source


def _update_url(page: EventPage) -> str:
    return reverse("wagtail_localize:update_translations", args=(_source(page).pk,))


def _refused(response: Any) -> bool:
    if response.status_code == 403:
        return True
    location: str = response.get("Location", "")
    return bool(response.status_code == 302 and location == reverse("wagtailadmin_home"))


# --- The model refuses to publish a hidden page


def test_a_hidden_page_is_never_published_outside_restore() -> None:
    page, english = _hidden_translated()
    for hidden in (page, english):
        revision = hidden.get_latest_revision()
        with pytest.raises(PermissionDenied):
            revision.publish()
        assert not _fresh(hidden).live


def test_restore_still_publishes_every_language() -> None:
    page, english = _hidden_translated()
    restore_event(UserFactory.create(superuser=True), page)
    assert _fresh(page).live
    assert _fresh(english).live
    assert _fresh(page).status == EventPage.Status.PUBLISHED


# --- wagtail-localize's views


def test_a_non_staff_editor_cannot_publish_a_hidden_event_through_update_translations(
    client: Client,
) -> None:
    page, english = _hidden_translated()
    verified_login(client, _editor())
    assert _refused(client.get(_update_url(page)))
    response = client.post(_update_url(page), {"publish_translations": "on"})
    assert _refused(response)
    for hidden in (page, english):
        fresh = _fresh(hidden)
        assert not fresh.live
        assert fresh.hidden_at is not None
        assert fresh.status == EventPage.Status.HIDDEN


def test_a_non_staff_editor_cannot_translate_or_stop_translating_a_hidden_event(
    client: Client,
) -> None:
    page, english = _hidden_translated()
    verified_login(client, _editor())
    submit = reverse("wagtail_localize:submit_page_translation", args=(page.pk,))
    assert _refused(client.get(submit))
    translation, _created = Translation.objects.get_or_create(
        source=_source(page), target_locale=english.locale
    )
    stop = reverse("wagtail_localize:stop_translation", args=(translation.pk,))
    assert _refused(client.post(stop))
    assert Translation.objects.get(pk=translation.pk).enabled


def test_a_non_staff_editor_cannot_publish_translations_without_review(client: Client) -> None:
    page, english = _live_translated()
    assert page.vendor is not None
    VendorEventPolicy.objects.filter(vendor=page.vendor).update(mode=PolicyMode.ALWAYS_REVIEW)
    before = english.get_latest_revision().pk
    verified_login(client, _editor())
    response = client.post(_update_url(page), {"publish_translations": "on"})
    assert _refused(response)
    assert english.get_latest_revision().pk == before


def test_a_non_staff_editor_still_updates_translation_drafts_without_publishing(
    client: Client,
) -> None:
    page, _english = _live_translated()
    assert page.vendor is not None
    VendorEventPolicy.objects.filter(vendor=page.vendor).update(mode=PolicyMode.ALWAYS_REVIEW)
    verified_login(client, _editor())
    assert client.get(_update_url(page)).status_code == 200
    response = client.post(_update_url(page), {})
    assert response.status_code == 302
    assert not _refused(response)


def test_staff_still_use_update_translations_on_a_hidden_event(client: Client) -> None:
    page, _english = _hidden_translated()
    verified_login(client, UserFactory.create(superuser=True))
    assert client.get(_update_url(page)).status_code == 200


def test_a_non_staff_publisher_cannot_publish_from_the_translation_editor_without_review(
    client: Client,
) -> None:
    """A synced translation opens in wagtail-localize's editor, which posts `action=publish`."""
    page, english = _live_translated()
    assert page.vendor is not None
    VendorEventPolicy.objects.filter(vendor=page.vendor).update(mode=PolicyMode.ALWAYS_REVIEW)
    editor = _editor()
    group = Group.objects.create(name=f"Event publishers {secrets.token_hex(4)}")
    GroupPagePermission.objects.create(
        group=group,
        page=Page.get_first_root_node(),
        permission=Permission.objects.get(
            content_type__app_label="wagtailcore", codename="publish_page"
        ),
    )
    editor.groups.add(group)
    before = english.get_latest_revision().pk
    verified_login(client, editor)
    edit = reverse("wagtailadmin_pages:edit", args=(english.pk,))
    response = client.post(edit, {"action": "publish"})
    assert _refused(response)
    assert english.get_latest_revision().pk == before
