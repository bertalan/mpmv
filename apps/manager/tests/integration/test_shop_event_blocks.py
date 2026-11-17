"""EVT.4 part 2: the vendor's event body editor, images, map picker, choosers and preview.

The block editor works on the latest revision of the vendor's own event: each add, edit, move or
remove is one `save_event_draft` call, so the EVT.3 service checks (vendor-safe blocks, own images,
own products, https links) and the CMS.12 sanitiser run for every change. A negative tenant test
covers every new view.
"""

import io
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from wagtail.models import Collection

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.catalog.tests.factories import (
    CategoryFactory,
    ProductFactory,
    ProductTranslationFactory,
)
from apps.core.geocoding import AddressQuery
from apps.core.geocoding import engine as geo_engine
from apps.core.i18n import primary_language
from apps.core.models import AuditLog, ImageLicence, MediaImage
from apps.core.tests.factories import (
    GeocodeCacheEntryFactory,
    ImageLicenceFactory,
    MediaImageFactory,
)
from apps.events import engine
from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory, VendorMemberFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _index() -> None:
    primary_index()


@pytest.fixture
def vendor(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop One", status=Vendor.Status.ACTIVE)


@pytest.fixture
def other_vendor(db: None) -> Vendor:
    return VendorFactory.create(display_name="Shop Two", status=Vendor.Status.ACTIVE)


@pytest.fixture
def owner(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="owner").user


@pytest.fixture
def customer(db: None) -> User:
    from apps.accounts.tests.factories import UserFactory

    return UserFactory.create()


@pytest.fixture
def signed_in(client: Client, owner: User) -> Client:
    verified_login(client, owner)
    return client


def _product(vendor: Vendor, title: str = "", **fields: Any) -> Any:
    """A product of `vendor`; `title` is its English text (a product has no name column)."""
    product = ProductFactory.create(vendor=vendor, **fields)
    if title:
        ProductTranslationFactory.create(product=product, language=primary_language(), title=title)
    return product


def _event(vendor: Vendor, **fields: Any) -> EventPage:
    fields.setdefault("body", [])  # the factory's own paragraph would hide what the tests add
    page: EventPage = EventPageFactory.create(vendor=vendor, **fields)
    page.save_revision()
    return page


def _body(page: EventPage) -> list[dict[str, Any]]:
    latest: EventPage = EventPage.objects.get(pk=page.pk).get_latest_revision_as_object()
    return [dict(block) for block in latest.body.get_prep_value()]


def _collection(vendor: Vendor) -> Collection:
    root = Collection.get_first_root_node() or Collection.add_root(name="Root")
    return root.add_child(name=engine.vendor_collection_name(str(vendor.public_id)))


def _own_image(vendor: Vendor) -> MediaImage:
    existing = Collection.objects.filter(
        depth=2, name=engine.vendor_collection_name(str(vendor.public_id))
    ).first()
    image: MediaImage = MediaImageFactory.create(collection=existing or _collection(vendor))
    return image


def _url(name: str, page: EventPage, **kwargs: Any) -> str:
    return reverse(f"manager:shop:{name}", kwargs={"public_id": page.public_id, **kwargs})


def _add(client: Client, page: EventPage, kind: str, /, **fields: Any) -> Any:
    return client.post(_url("event_block_add", page), {"type": kind, **fields})


def _paragraph(client: Client, page: EventPage, text: str = "Hello fair") -> str:
    assert _add(client, page, "paragraph", text=text).status_code in (200, 302)
    return str(_body(page)[-1]["id"])


# --- Add, edit, move, remove


def test_the_edit_page_shows_the_block_editor_and_the_new_page_asks_to_save_first(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    html = signed_in.get(reverse("manager:shop:event_edit", args=[page.public_id])).text
    assert 'id="event-blocks"' in html
    assert _url("event_block_add", page) in html
    new = signed_in.get(reverse("manager:shop:event_new")).text
    assert 'id="event-blocks"' not in new
    assert "Save the event first" in new


def test_adding_a_paragraph_stores_a_block_in_a_new_revision(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    response = _add(signed_in, page, "paragraph", text="Come and meet us.\n\nBring a friend.")
    assert response.status_code == 302
    body = _body(page)
    assert [block["type"] for block in body] == ["paragraph"]
    assert "<p" in body[0]["value"]
    assert "Come and meet us." in body[0]["value"]
    assert "Bring a friend." in body[0]["value"]
    assert AuditLog.objects.filter(action="events.event_draft_saved").exists()


def test_rich_text_is_sanitised_and_unsafe_links_are_refused(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    response = _add(
        signed_in, page, "paragraph", text='<p>Hi <a href="javascript:alert(1)">there</a></p>'
    )
    assert response.status_code == 200  # the form is shown again with the error
    assert "javascript:" in response.text or "Links must not" in response.text
    assert _body(page) == []
    ok = _add(
        signed_in,
        page,
        "paragraph",
        text="<h2>News</h2><p><strong>Bold</strong> <script>x()</script></p>",
    )
    assert ok.status_code == 302
    stored = _body(page)[0]["value"]
    assert "<script" not in stored
    assert "News" in stored


def test_a_block_type_outside_the_vendor_safe_set_is_not_offered(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    for kind in ("embed", "countdown", "raw_html", "hero"):
        assert signed_in.get(_url("event_block_add", page) + f"?type={kind}").status_code == 404
        assert _add(signed_in, page, kind, text="x").status_code == 404
    assert _body(page) == []
    html = signed_in.get(reverse("manager:shop:event_edit", args=[page.public_id])).text
    for kind in engine.VENDOR_SAFE_BLOCKS:
        assert f'value="{kind}"' in html


def test_a_figure_uses_an_own_image_and_needs_an_alt_text(
    signed_in: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    page = _event(vendor)
    mine, theirs = _own_image(vendor), _own_image(other_vendor)
    form = signed_in.get(_url("event_block_add", page) + "?type=figure").text
    assert f'value="{mine.pk}"' in form
    assert f'value="{theirs.pk}"' not in form
    refused = _add(signed_in, page, "figure", image=theirs.pk, alt_text="Their stand")
    assert refused.status_code == 200
    assert _body(page) == []
    missing = _add(signed_in, page, "figure", image=mine.pk, alt_text="")
    assert missing.status_code == 200
    assert _body(page) == []
    assert _add(signed_in, page, "figure", image=mine.pk, alt_text="Our stand").status_code == 302
    value = _body(page)[0]["value"]
    assert value["image"] == mine.pk
    assert value["alt_text"] == "Our stand"


def test_a_gallery_takes_own_images_and_skips_empty_slots(
    signed_in: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    page = _event(vendor)
    one, two, foreign = _own_image(vendor), _own_image(vendor), _own_image(other_vendor)
    bad = _add(
        signed_in,
        page,
        "gallery",
        image_1=one.pk,
        alt_text_1="A",
        image_2=foreign.pk,
        alt_text_2="B",
    )
    assert bad.status_code == 200
    assert _body(page) == []
    ok = _add(
        signed_in, page, "gallery", image_1=one.pk, alt_text_1="A", image_3=two.pk, alt_text_3="C"
    )
    assert ok.status_code == 302
    images = _body(page)[0]["value"]["images"]
    assert [
        item["value"]["image"] if "value" in item and "image" not in item else item["image"]
        for item in images
    ] == [one.pk, two.pk]


def test_a_cta_takes_an_https_url_or_one_of_the_vendors_own_events(
    signed_in: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    page = _event(vendor)
    mine = _event(vendor, title="Our other fair")
    theirs = _event(other_vendor, title="Rival fair")
    plain = {"heading": "Visit us", "label": "Read more"}
    assert _add(signed_in, page, "cta", url="http://example.com/", **plain).status_code == 200
    assert _add(signed_in, page, "cta", page=theirs.pk, **plain).status_code == 200
    assert _add(signed_in, page, "cta", url="https://example.com/a", **plain).status_code == 302
    assert _add(signed_in, page, "cta", page=mine.pk, **plain).status_code == 302
    links = [block["value"]["link"] for block in _body(page)]
    assert links[0]["url"] == "https://example.com/a"
    assert links[1]["page"] == mine.pk
    form = signed_in.get(_url("event_block_add", page) + "?type=cta").text
    assert "Rival fair" not in form
    assert "Our other fair" in form


def test_product_cards_offer_and_accept_the_shops_own_products_only(
    signed_in: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    page = _event(vendor)
    mine = _product(vendor, "Own mug")
    theirs = _product(other_vendor, "Their mug")
    form = signed_in.get(_url("event_block_add", page) + "?type=product_cards").text
    assert "Own mug" in form
    assert "Their mug" not in form
    refused = _add(signed_in, page, "product_cards", layout="grid", products=[mine.pk, theirs.pk])
    assert refused.status_code == 200
    assert _body(page) == []
    assert (
        _add(signed_in, page, "product_cards", layout="row", products=[mine.pk]).status_code == 302
    )
    products = _body(page)[0]["value"]["products"]
    assert [p["value"] if isinstance(p, dict) else p for p in products] == [mine.pk]


def test_a_product_grid_is_always_sourced_from_the_shop_itself(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    response = _add(signed_in, page, "product_grid", heading="Newest", limit="4", see_all="on")
    assert response.status_code == 302
    value = _body(page)[0]["value"]
    assert value["source"][0]["type"] == "vendor"
    assert value["source"][0]["value"] == vendor.pk
    # A crafted category or vendor source in the request is ignored: there is no such field.
    other = VendorFactory.create()
    _add(signed_in, page, "product_grid", limit="4", source_type="vendor", source=other.pk)
    assert all(b["value"]["source"][0]["value"] == vendor.pk for b in _body(page))


def test_a_faq_keeps_the_filled_questions(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    response = _add(
        signed_in,
        page,
        "faq",
        heading="Questions",
        question_1="Where?",
        answer_1="At the fair.",
        question_3="When?",
        answer_3="On Saturday.",
    )
    assert response.status_code == 302
    items = _body(page)[0]["value"]["items"]
    assert len(items) == 2
    assert _add(signed_in, page, "faq", heading="None").status_code == 200  # needs a question


def test_editing_a_block_shows_its_value_and_saves_the_change(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    block_id = _paragraph(signed_in, page, "First words")
    url = _url("event_block_edit", page, block_id=block_id)
    assert "First words" in signed_in.get(url).text
    assert signed_in.post(url, {"type": "paragraph", "text": "Second words"}).status_code == 302
    body = _body(page)
    assert len(body) == 1
    assert body[0]["id"] == block_id
    assert "Second words" in body[0]["value"]


def test_blocks_move_up_and_down_and_are_removed(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    first = _paragraph(signed_in, page, "One")
    second = _paragraph(signed_in, page, "Two")

    def move(block_id: str, direction: str) -> Any:
        url = _url("event_block_move", page, block_id=block_id)
        return signed_in.post(url, {"direction": direction})

    assert move(second, "up").status_code == 302
    assert [b["id"] for b in _body(page)] == [second, first]
    assert move(second, "up").status_code == 302  # already first: nothing changes, no error
    assert [b["id"] for b in _body(page)] == [second, first]
    assert move(second, "down").status_code == 302
    assert [b["id"] for b in _body(page)] == [first, second]
    assert move(first, "sideways").status_code == 400
    assert signed_in.post(_url("event_block_remove", page, block_id=first)).status_code == 302
    assert [b["id"] for b in _body(page)] == [second]


def test_an_unknown_block_id_is_a_404(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    for name in ("event_block_edit", "event_block_move", "event_block_remove"):
        url = _url(name, page, block_id=str(uuid4()))
        assert (
            signed_in.post(url, {"direction": "up", "type": "paragraph", "text": "x"}).status_code
            == 404
        )
    assert signed_in.get(_url("event_block_edit", page, block_id=str(uuid4()))).status_code == 404


def test_htmx_gets_the_panel_only_and_a_plain_post_is_redirected(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    htmx = signed_in.post(
        _url("event_block_add", page),
        {"type": "paragraph", "text": "Via htmx"},
        HTTP_HX_REQUEST="true",
    )
    assert htmx.status_code == 200
    html = htmx.text
    assert "<html" not in html
    assert 'id="event-blocks"' in html
    assert "Via htmx" in html
    plain = _add(signed_in, page, "paragraph", text="Plain")
    assert plain.status_code == 302
    assert plain["Location"].startswith(reverse("manager:shop:event_edit", args=[page.public_id]))


def test_every_block_action_is_a_button_with_the_block_in_its_name(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    _paragraph(signed_in, page, "Keyboard text")
    html = signed_in.get(reverse("manager:shop:event_edit", args=[page.public_id])).text
    for label in ("Move up", "Move down", "Remove", "Edit"):
        assert label in html
    assert "hx-post" in html


def test_a_page_awaiting_review_keeps_its_body(
    signed_in: Client, owner: User, vendor: Vendor
) -> None:
    from apps.events import services

    page = _event(vendor)
    services.submit_event(owner, page)
    response = _add(signed_in, page, "paragraph", text="Sneaky")
    assert response.status_code == 200
    assert "awaiting review" in response.text
    assert _body(page) == []


# --- Images


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), "teal").save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(client: Client, **changes: Any) -> Any:
    licence = ImageLicence.objects.filter(code="own-work").first() or ImageLicenceFactory.create(
        code="own-work", name="Own work", source_required=False
    )
    data: dict[str, Any] = {
        "file": SimpleUploadedFile("stand.png", _png(), content_type="image/png"),
        "title": "Our stand",
        "licence": licence.pk,
        "author": "Shop One",
    }
    data.update(changes)
    return client.post(reverse("manager:shop:event_images"), data)


def test_an_upload_lands_in_the_shops_own_collection_with_its_licence(
    signed_in: Client, vendor: Vendor
) -> None:
    response = _upload(signed_in)
    assert response.status_code == 302
    image = MediaImage.objects.get(title="Our stand")
    assert image.collection.name == engine.vendor_collection_name(str(vendor.public_id))
    assert image.licence is not None
    assert image.licence.code == "own-work"
    assert image.author == "Shop One"
    assert AuditLog.objects.filter(action="events.image_uploaded").count() == 1
    assert "Our stand" in signed_in.get(reverse("manager:shop:event_images")).text


def test_an_upload_without_the_licence_metadata_is_refused(
    signed_in: Client, vendor: Vendor
) -> None:
    for changes in ({"author": ""}, {"licence": ""}, {"licence": 0}):
        assert _upload(signed_in, **changes).status_code == 200
    credit = ImageLicenceFactory.create(code="CC-BY-4.0", name="CC BY 4.0", source_required=True)
    assert _upload(signed_in, licence=credit.pk, source_url="").status_code == 200
    assert not MediaImage.objects.filter(title="Our stand").exists()
    ok = _upload(signed_in, licence=credit.pk, source_url="https://example.com/photo")
    assert ok.status_code == 302


def test_a_file_that_is_not_an_image_is_refused(signed_in: Client, vendor: Vendor) -> None:
    fake = SimpleUploadedFile("stand.png", b"<script>alert(1)</script>", content_type="image/png")
    assert _upload(signed_in, file=fake).status_code == 200
    svg = SimpleUploadedFile(
        "a.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/svg+xml"
    )
    assert _upload(signed_in, file=svg).status_code == 200
    assert not MediaImage.objects.filter(title="Our stand").exists()


def test_the_library_lists_the_shops_own_images_only(
    signed_in: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    mine, theirs = _own_image(vendor), _own_image(other_vendor)
    html = signed_in.get(reverse("manager:shop:event_images")).text
    assert mine.title in html
    assert theirs.title not in html


def _file_errors(response: Any) -> list[str]:
    assert response.status_code == 200
    return list(response.context["form"].errors.get("file", []))


def test_the_31st_upload_in_an_hour_is_refused_with_an_error_on_the_file(
    signed_in: Client, vendor: Vendor
) -> None:
    from apps.events.services import EVENT_IMAGE_LIMIT

    assert EVENT_IMAGE_LIMIT == 30
    for number in range(EVENT_IMAGE_LIMIT):
        assert _upload(signed_in, title=f"Stand {number}").status_code == 302
    response = _upload(signed_in, title="One too many")
    assert any("Too many uploads" in message for message in _file_errors(response))
    assert not MediaImage.objects.filter(title="One too many").exists()
    assert MediaImage.objects.filter(title__startswith="Stand ").count() == EVENT_IMAGE_LIMIT


def test_an_oversized_file_is_refused(signed_in: Client, vendor: Vendor, settings: Any) -> None:
    from PIL import Image

    settings.WAGTAILIMAGES_MAX_UPLOAD_SIZE = 2048
    noise = io.BytesIO()
    Image.effect_noise((120, 120), 80).convert("RGB").save(noise, format="PNG")
    assert len(noise.getvalue()) > 2048
    big = SimpleUploadedFile("big.png", noise.getvalue(), content_type="image/png")
    assert _file_errors(_upload(signed_in, file=big))
    assert not MediaImage.objects.filter(title="Our stand").exists()


def test_an_svg_is_refused_whatever_its_name_and_type(signed_in: Client, vendor: Vendor) -> None:
    body = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    for name, kind in (("a.svg", "image/svg+xml"), ("a.png", "image/png"), ("a.jpg", "image/jpeg")):
        upload = SimpleUploadedFile(name, body, content_type=kind)
        assert _file_errors(_upload(signed_in, file=upload)), name
    assert not MediaImage.objects.filter(title="Our stand").exists()


def test_a_png_that_holds_html_is_refused(signed_in: Client, vendor: Vendor) -> None:
    html = b"<!doctype html><html><body><script>alert(document.cookie)</script></body></html>"
    spoofed = SimpleUploadedFile("photo.png", html, content_type="image/png")
    assert _file_errors(_upload(signed_in, file=spoofed))
    assert not MediaImage.objects.filter(title="Our stand").exists()


def test_the_library_is_paged(signed_in: Client, vendor: Vendor) -> None:
    from apps.manager.event_block_views import IMAGES_PER_PAGE

    for _number in range(IMAGES_PER_PAGE + 1):
        _own_image(vendor)
    url = reverse("manager:shop:event_images")
    first = signed_in.get(url)
    assert len(first.context["images"]) == IMAGES_PER_PAGE
    assert 'rel="next"' in first.text
    assert "<img" in first.text  # a thumbnail
    assert len(signed_in.get(url, {"page": 2}).context["images"]) == 1
    assert signed_in.get(url, {"page": "nonsense"}).status_code == 200


def test_an_upload_without_the_events_permission_is_a_form_error(
    signed_in: Client, vendor: Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.core.exceptions import PermissionDenied

    from apps.events import services

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise PermissionDenied("no")

    monkeypatch.setattr(services, "upload_event_image", refuse)
    response = _upload(signed_in)
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()


# --- Concurrent edits and the confirmation pages


def _base(page: EventPage) -> int:
    from apps.events import services

    return services.event_revision_id(page)


def test_the_panel_and_the_forms_carry_the_revision_they_were_rendered_from(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    block_id = _paragraph(signed_in, page)
    edit = reverse("manager:shop:event_edit", args=[page.public_id])
    assert f'name="base_revision" value="{_base(page)}"' in signed_in.get(edit).text
    form = signed_in.get(_url("event_block_edit", page, block_id=block_id)).text
    assert f'name="base_revision" value="{_base(page)}"' in form


def test_a_stale_edit_is_refused_and_nothing_is_lost(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    block_id = _paragraph(signed_in, page, "Original")
    opened = _base(page)  # both editors open on this revision
    edit_url = _url("event_block_edit", page, block_id=block_id)
    ok = signed_in.post(
        edit_url, {"type": "paragraph", "text": "First editor", "base_revision": opened}
    )
    assert ok.status_code == 302
    assert _base(page) != opened
    stale = signed_in.post(
        edit_url, {"type": "paragraph", "text": "Second editor", "base_revision": opened}
    )
    assert stale.status_code == 200
    assert "changed by someone else" in stale.text
    assert "Second editor" in stale.text  # what was typed is shown again, not lost
    body = _body(page)
    assert len(body) == 1
    assert "First editor" in body[0]["value"]
    assert "Second editor" not in body[0]["value"]


def test_a_stale_add_move_and_remove_are_refused(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    first = _paragraph(signed_in, page, "One")
    second = _paragraph(signed_in, page, "Two")
    opened = _base(page)
    assert _paragraph(signed_in, page, "Three")  # someone else adds a block
    before = _body(page)
    stale = {"base_revision": opened}
    assert _add(signed_in, page, "paragraph", text="Late", **stale).status_code == 200
    move = signed_in.post(
        _url("event_block_move", page, block_id=second), {"direction": "up", **stale}
    )
    assert move.status_code == 302
    remove = signed_in.post(_url("event_block_remove", page, block_id=first), stale)
    assert remove.status_code == 302
    assert _body(page) == before
    htmx = signed_in.post(
        _url("event_block_remove", page, block_id=first), stale, HTTP_HX_REQUEST="true"
    )
    assert htmx.status_code == 409
    assert htmx["HX-Refresh"] == "true"
    assert _body(page) == before
    fresh = {"base_revision": _base(page)}
    assert (
        signed_in.post(_url("event_block_remove", page, block_id=first), fresh).status_code == 302
    )
    assert _body(page)[0]["id"] == second


def test_the_change_locks_the_page_row(signed_in: Client, vendor: Vendor) -> None:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    page = _event(vendor)
    with CaptureQueriesContext(connection) as queries:
        _paragraph(signed_in, page)
    assert any("FOR UPDATE" in q["sql"] and "events_eventpage" in q["sql"] for q in queries)


def test_a_bare_add_link_lists_the_block_types_and_an_unknown_type_is_a_404(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    response = signed_in.get(_url("event_block_add", page))
    assert response.status_code == 200
    assert "?type=paragraph" in response.text
    assert signed_in.get(_url("event_block_add", page) + "?type=nonsense").status_code == 404


def test_removing_without_javascript_asks_first(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    block_id = _paragraph(signed_in, page, "Keep me")
    url = _url("event_block_remove", page, block_id=block_id)
    confirm = signed_in.get(url)
    assert confirm.status_code == 200
    assert "Remove this block?" in confirm.text
    assert "Keep me" in confirm.text
    assert len(_body(page)) == 1  # a GET removes nothing
    edit = reverse("manager:shop:event_edit", args=[page.public_id])
    panel = signed_in.get(edit).text
    assert f'method="get" action="{url}"' in panel  # the panel's button goes to the confirmation
    assert signed_in.post(url).status_code == 302
    assert _body(page) == []


# --- The map picker and the point


def _point(page: EventPage) -> EventPage:
    latest: EventPage = EventPage.objects.get(pk=page.pk).get_latest_revision_as_object()
    return latest


def _form(page: EventPage, **changes: Any) -> dict[str, Any]:
    data = {
        "title": page.title,
        "summary": "Meet the makers.",
        "event_type": "fair",
        "starts_at": "2027-10-02T09:00",
        "ends_at": "2027-10-02T18:00",
        "timezone": "Europe/Rome",
        "recurrence": "none",
        "attendance": "in_person",
        "city": "Milano",
        "country": "IT",
    }
    data.update(changes)
    return data


def test_the_form_carries_the_map_picker_and_the_shared_map_module(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    html = signed_in.get(reverse("manager:shop:event_edit", args=[page.public_id])).text
    assert "data-map-picker" in html
    assert "vendor/leaflet/leaflet.js" in html
    assert "events/map.js" in html
    assert reverse("geocoding_find") in html
    for name in ("latitude", "longitude", "geo_source", "picked_latitude", "picked_longitude"):
        assert f'name="{name}"' in html
    assert "data-latitude-input" in html
    assert "data-source-input" in html


def test_a_point_placed_on_the_map_or_typed_is_saved_with_its_source(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    url = reverse("manager:shop:event_edit", args=[page.public_id])
    signed_in.post(url, _form(page, latitude="45.464204", longitude="9.191383", geo_source="map"))
    saved = _point(page)
    assert (saved.latitude, saved.longitude) == (Decimal("45.464204"), Decimal("9.191383"))
    assert saved.geo_source == EventPage.GeoSource.MAP
    signed_in.post(url, _form(page, latitude="41.9", longitude="12.5"))  # typed, no source sent
    saved = _point(page)
    assert (saved.latitude, saved.longitude) == (Decimal("41.9"), Decimal("12.5"))
    assert saved.geo_source == EventPage.GeoSource.MANUAL
    shown = signed_in.get(url).text
    assert "41.900000" in shown or "41.9" in shown


def test_a_point_out_of_range_or_half_a_point_is_an_error(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    url = reverse("manager:shop:event_edit", args=[page.public_id])
    assert signed_in.post(url, _form(page, latitude="95", longitude="9")).status_code == 200
    assert signed_in.post(url, _form(page, latitude="45.1")).status_code == 200
    assert _point(page).latitude is None


def test_emptying_the_inputs_clears_the_point_and_a_promotion_has_no_place(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    url = reverse("manager:shop:event_edit", args=[page.public_id])
    signed_in.post(url, _form(page, latitude="45.4", longitude="9.1", geo_source="map"))
    signed_in.post(url, _form(page, latitude="", longitude="", geo_source=""))
    assert _point(page).latitude is None
    signed_in.post(url, _form(page, latitude="45.4", longitude="9.1", geo_source="map"))
    signed_in.post(url, _form(page, event_type="promotion", latitude="45.4", longitude="9.1"))
    assert _point(page).latitude is None


ADDRESS = AddressQuery(country="IT", postcode="20121", city="Milano", street="Via Esempio 1")


def _cache() -> None:
    GeocodeCacheEntryFactory.create(
        key=geo_engine.cache_key(ADDRESS),
        language=primary_language(),
        normalised_address=geo_engine.normalised_address(ADDRESS),
        provider="photon",
        candidates=[
            {
                "latitude": "45.464270",
                "longitude": "9.189510",
                "label": "Via Esempio 1, 20121 Milano",
                "precision": "rooftop",
                "confidence": 0.8,
                "provider": "photon",
            }
        ],
    )


def _address(**changes: Any) -> dict[str, Any]:
    return {"street": ADDRESS.street, "postcode": ADDRESS.postcode, "city": ADDRESS.city, **changes}


def test_a_chosen_candidate_keeps_its_provider_and_precision_also_when_moved(
    signed_in: Client, vendor: Vendor
) -> None:
    _cache()
    page = _event(vendor)
    url = reverse("manager:shop:event_edit", args=[page.public_id])
    chosen = {"latitude": "45.464270", "longitude": "9.189510", "geo_source": "geocoded"}
    signed_in.post(url, _form(page, **_address(**chosen)))
    saved = _point(page)
    assert (saved.geo_provider, saved.geo_precision) == ("photon", "rooftop")
    assert saved.geo_source == EventPage.GeoSource.GEOCODED
    moved = {
        "latitude": "45.464900",
        "longitude": "9.190000",
        "geo_source": "map",
        "picked_latitude": "45.464270",
        "picked_longitude": "9.189510",
    }
    other = _event(vendor, title="Second")
    signed_in.post(
        reverse("manager:shop:event_edit", args=[other.public_id]),
        _form(other, **_address(**moved)),
    )
    kept = _point(other)
    assert kept.geo_source == EventPage.GeoSource.MAP
    assert (kept.geo_provider, kept.geo_precision) == ("photon", "rooftop")


def test_a_geocoded_point_no_lookup_returned_is_refused(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor)
    url = reverse("manager:shop:event_edit", args=[page.public_id])
    forged = {"latitude": "10.1", "longitude": "10.1", "geo_source": "geocoded"}
    response = signed_in.post(url, _form(page, **_address(**forged)))
    assert response.status_code == 200
    assert "Find coordinates" in response.text
    assert _point(page).geo_provider == ""


# --- Product and category choosers


def test_the_choosers_offer_the_shops_own_products_and_their_categories_only(
    signed_in: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    page = _event(vendor)
    own_category, foreign_category = CategoryFactory.create(), CategoryFactory.create()
    _product(vendor, "Own mug", category=own_category)
    _product(other_vendor, "Their mug", category=foreign_category)
    html = signed_in.get(reverse("manager:shop:event_edit", args=[page.public_id])).text
    assert "Own mug" in html
    assert "Their mug" not in html
    assert f'value="{own_category.pk}"' in html
    assert f'value="{foreign_category.pk}"' not in html


def test_linking_products_and_categories_saves_them_and_refuses_foreign_ones(
    signed_in: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    page = _event(vendor)
    url = reverse("manager:shop:event_edit", args=[page.public_id])
    category = CategoryFactory.create()
    mine = ProductFactory.create(vendor=vendor, category=category)
    theirs = ProductFactory.create(vendor=other_vendor)
    refused = signed_in.post(url, _form(page, products=[mine.pk, theirs.pk]))
    assert refused.status_code == 200
    assert [p.pk for p in _point(page).products.all()] == []
    ok = signed_in.post(url, _form(page, products=[mine.pk], categories=[category.pk]))
    assert ok.status_code == 302
    saved = _point(page)
    assert [p.pk for p in saved.products.all()] == [mine.pk]
    assert [c.pk for c in saved.categories.all()] == [category.pk]
    foreign_category = CategoryFactory.create()
    ProductFactory.create(vendor=other_vendor, category=foreign_category)
    assert signed_in.post(url, _form(page, categories=[foreign_category.pk])).status_code == 200


def test_saving_the_structured_form_keeps_the_body_and_the_links(
    signed_in: Client, vendor: Vendor
) -> None:
    page = _event(vendor)
    product = ProductFactory.create(vendor=vendor)
    _paragraph(signed_in, page, "Body stays")
    url = reverse("manager:shop:event_edit", args=[page.public_id])
    signed_in.post(url, _form(page, title="Retitled", products=[product.pk]))
    signed_in.post(url, _form(page, title="Retitled again", products=[product.pk]))
    assert len(_body(page)) == 1
    assert [p.pk for p in _point(page).products.all()] == [product.pk]


# --- Preview


def test_the_preview_shows_the_latest_revision_read_only(signed_in: Client, vendor: Vendor) -> None:
    page = _event(vendor, title="Published title")
    page.get_latest_revision().publish()
    draft = EventPage.objects.get(pk=page.pk).get_latest_revision_as_object()
    draft.title = "Draft title"
    draft.save_revision()
    response = signed_in.get(_url("event_preview", page))
    assert response.status_code == 200
    assert "Draft title" in response.text
    assert "noindex" in response.headers.get("X-Robots-Tag", "") or "noindex" in response.text
    assert signed_in.post(_url("event_preview", page)).status_code == 405
    assert (
        "Preview" in signed_in.get(reverse("manager:shop:event_edit", args=[page.public_id])).text
    )


# --- Negative tenant tests on every new view


def _every_view(page: EventPage) -> list[tuple[str, str]]:
    block = str(uuid4())
    return [
        ("get", _url("event_block_add", page)),
        ("post", _url("event_block_add", page)),
        ("get", _url("event_block_edit", page, block_id=block)),
        ("post", _url("event_block_edit", page, block_id=block)),
        ("post", _url("event_block_move", page, block_id=block)),
        ("post", _url("event_block_remove", page, block_id=block)),
        ("get", _url("event_preview", page)),
    ]


SHOP_WIDE = [("get", "manager:shop:event_images"), ("post", "manager:shop:event_images")]


def _payload() -> dict[str, Any]:
    return {"type": "paragraph", "text": "Taken over", "direction": "up"}


def test_another_shops_member_gets_a_404_on_every_new_view(
    client: Client, vendor: Vendor, other_vendor: Vendor
) -> None:
    page = _event(vendor, title="Ours")
    _paragraph_as_owner(vendor, page)
    stranger = VendorMemberFactory.create(vendor=other_vendor, role="owner").user
    verified_login(client, stranger)
    for method, url in _every_view(page):
        assert getattr(client, method)(url, _payload()).status_code == 404, (method, url)
    assert [b["type"] for b in _body(page)] == ["paragraph"]


def _paragraph_as_owner(vendor: Vendor, page: EventPage) -> None:
    from apps.events import services

    owner = VendorMemberFactory.create(vendor=vendor, role="owner").user
    services.save_event_draft(
        owner, {"body": [{"type": "paragraph", "value": "<p>Mine</p>"}]}, page, vendor=vendor
    )


def test_a_customer_gets_a_403_and_an_anonymous_visitor_a_login_redirect(
    client: Client, customer: User, vendor: Vendor
) -> None:
    page = _event(vendor)
    views = [*_every_view(page), *[(m, reverse(n)) for m, n in SHOP_WIDE]]
    for method, url in views:
        response = getattr(client, method)(url, _payload())
        assert response.status_code == 302, (method, url)
        assert "/account/login/" in response["Location"]
    verified_login(client, customer)
    for method, url in views:
        assert getattr(client, method)(url, _payload()).status_code == 403, (method, url)
    assert _body(page) == []


def test_vendor_staff_may_edit_blocks_and_upload(client: Client, vendor: Vendor) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    verified_login(client, staff)
    page = _event(vendor)
    assert _add(client, page, "paragraph", text="By staff").status_code == 302
    assert _upload(client).status_code == 302


# --- The ride theme


def test_the_new_pages_render_under_the_ride_theme(
    signed_in: Client, vendor: Vendor, settings: Any
) -> None:
    # The site default theme, not apps.themes: the "No app imports themes" contract.
    settings.MPMV_THEME_DEFAULT = "ride"
    page = _event(vendor)
    _paragraph(signed_in, page, "Ride text")
    block_id = str(_body(page)[0]["id"])
    urls = [
        reverse("manager:shop:event_edit", args=[page.public_id]),
        _url("event_block_add", page) + "?type=figure",
        _url("event_block_edit", page, block_id=block_id),
        reverse("manager:shop:event_images"),
    ]
    for url in urls:
        response = signed_in.get(url)
        assert response.status_code == 200, url
        assert 'data-theme="ride"' in response.text, url
    html = signed_in.get(urls[0]).text
    assert 'id="event-blocks"' in html
    assert "data-map-picker" in html
