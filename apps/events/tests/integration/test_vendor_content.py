"""EVT.3 security fix: what a vendor may put in an event, enforced in the service so every entry
point (vendor area, API, scripts) gets it, and the in-transaction vendor check (CORE.14)."""

import datetime as dt
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from wagtail.models import Collection, Page

from apps.accounts.tests.factories import UserFactory
from apps.core.tests.factories import MediaImageFactory
from apps.events import engine
from apps.events.models import EventPage
from apps.events.services import cancel_event, save_event_draft, submit_event
from apps.events.tests.factories import EventPageFactory
from apps.events.tests.integration.test_services import _create, _data, _owner, _vendor_of
from apps.vendors.engine import VendorCannotWrite
from apps.vendors.models import Vendor

pytestmark = pytest.mark.django_db


def _vendor_collection(vendor: Vendor) -> Collection:
    root = Collection.get_first_root_node() or Collection.add_root(name="Root")
    return root.add_child(name=engine.vendor_collection_name(str(vendor.public_id)))


def _figure(image_id: int) -> dict[str, Any]:
    return {"type": "figure", "value": {"image": image_id, "alt_text": "A stand"}}


def _refused(owner: Any, body: list[dict[str, Any]], page: EventPage | None = None) -> None:
    def attempt() -> None:
        if page is None:
            save_event_draft(owner, _data(body=body), vendor=_vendor_of(owner))
        else:
            save_event_draft(owner, {"body": body}, page)

    with pytest.raises(ValidationError) as excinfo:
        attempt()
    assert "body" in excinfo.value.message_dict


# --- B2: own images only


def test_a_vendor_may_use_an_image_of_its_own_collection() -> None:
    owner = _owner()
    image = MediaImageFactory.create(collection=_vendor_collection(_vendor_of(owner)))
    page = _create(owner, body=[_figure(image.pk)])
    assert page.get_latest_revision_as_object().body[0].value["image"].pk == image.pk


def test_a_vendor_may_use_a_sub_collection_of_its_own() -> None:
    owner = _owner()
    sub = _vendor_collection(_vendor_of(owner)).add_child(name="spring")
    image = MediaImageFactory.create(collection=sub)
    _create(owner, body=[_figure(image.pk)])


def test_a_vendor_cannot_use_a_root_collection_image() -> None:
    _refused(_owner(), [_figure(MediaImageFactory.create().pk)])


def test_a_vendor_cannot_use_another_vendors_image() -> None:
    owner = _owner()
    foreign = MediaImageFactory.create(collection=_vendor_collection(_vendor_of(_owner())))
    _refused(owner, [_figure(foreign.pk)])


def test_a_vendor_cannot_use_a_foreign_image_in_a_gallery_or_on_edit() -> None:
    owner = _owner()
    mine = MediaImageFactory.create(collection=_vendor_collection(_vendor_of(owner)))
    foreign = MediaImageFactory.create()
    page = _create(owner, body=[_figure(mine.pk)])
    gallery = {
        "type": "gallery",
        "value": {
            "images": [
                {"image": mine.pk, "alt_text": "a"},
                {"image": foreign.pk, "alt_text": "b"},
            ]
        },
    }
    _refused(owner, [gallery], page)


def test_a_missing_image_is_refused_like_a_foreign_one() -> None:
    _refused(_owner(), [_figure(999_999)])


def test_a_vendor_rich_text_cannot_embed_an_image_or_link_a_document() -> None:
    owner = _owner()
    foreign = MediaImageFactory.create()
    embed = f'<embed embedtype="image" id="{foreign.pk}"/>'
    _refused(owner, [{"type": "paragraph", "value": embed}])
    _refused(owner, [{"type": "paragraph", "value": '<a linktype="document" id="1">x</a>'}])


def test_staff_may_use_any_image() -> None:
    staff = _owner()
    staff.is_staff = True
    staff.save()
    image = MediaImageFactory.create()
    save_event_draft(staff, _data(body=[_figure(image.pk)]), vendor=None)


# --- M4: CTA links


def _cta(**link: Any) -> dict[str, Any]:
    return {
        "type": "cta",
        "value": {"heading": "Visit", "text": "", "link": {"label": "Go", **link}},
    }


def test_a_vendor_cta_may_use_an_https_url() -> None:
    _create(_owner(), body=[_cta(url="https://shop.example/offer")])


def test_a_vendor_cta_refuses_http_and_script_urls() -> None:
    owner = _owner()
    for url in ("http://shop.example", "javascript:alert(1)", "data:text/html,x"):
        _refused(owner, [_cta(url=url)])


def test_a_vendor_cta_may_point_to_its_own_event_only() -> None:
    owner = _owner()
    mine = EventPageFactory.create(vendor=_vendor_of(owner))
    theirs = EventPageFactory.create(vendor=_vendor_of(_owner()))
    _create(owner, body=[_cta(page=mine.pk)])
    _refused(owner, [_cta(page=theirs.pk)])


def test_a_vendor_cta_refuses_any_other_page() -> None:
    other = Page.objects.exclude(pk__in=EventPage.objects.values("pk")).first()
    assert other is not None
    _refused(_owner(), [_cta(page=other.pk)])


# --- M3: in-transaction vendor check


def test_a_suspended_vendor_cannot_save_submit_or_cancel() -> None:
    owner = _owner()
    vendor = _vendor_of(owner)
    page = _create(owner)
    submit_event(UserFactory.create(superuser=True), page)  # live, so cancel can run
    Vendor.objects.filter(pk=vendor.pk).update(status=Vendor.Status.SUSPENDED)
    # The caller still holds the stale, active object: only the re-read inside the write stops it.
    with pytest.raises(VendorCannotWrite):
        save_event_draft(owner, {"title": "New title"}, page)
    with pytest.raises(VendorCannotWrite):
        save_event_draft(owner, _data(title="Another"), vendor=vendor)
    with pytest.raises(VendorCannotWrite):
        submit_event(owner, page)
    with pytest.raises(VendorCannotWrite):
        cancel_event(owner, page)


# --- Limits


def test_exdates_and_body_are_bounded() -> None:
    owner = _owner()
    start = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)
    many = [start + dt.timedelta(days=n) for n in range(engine.MAX_EXDATES + 1)]
    with pytest.raises(ValidationError) as excinfo:
        save_event_draft(
            owner, _data(rrule="FREQ=DAILY;COUNT=5", exdates=many), vendor=_vendor_of(owner)
        )
    assert "exdates" in excinfo.value.message_dict
    blocks = [{"type": "paragraph", "value": "<p>x</p>"}] * (engine.MAX_BODY_BLOCKS + 1)
    _refused(owner, blocks)
