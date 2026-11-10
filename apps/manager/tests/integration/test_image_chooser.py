"""VEN.31 part 1 (D-34): the Manager image chooser and upload into the vendor's own collection.

A vendor owner picks and uploads images from the storefront editor without Wagtail admin access.
The chooser lists only the vendor's own collection; anything that names another collection or
another vendor's image answers 404 and is audited; uploads go through the SEC.2 image checks.
"""

import json
import re
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from wagtail.images import get_image_model
from wagtail.models import Collection

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.core.tests.factories import MediaImageFactory
from apps.manager.forms import MANAGER_RICH_TEXT_FEATURES
from apps.vendors import engine
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory
from tests.upload_corpus import HTML_PAGE, SVG_WITH_SCRIPT, png

from .conftest import SHOP

pytestmark = pytest.mark.django_db

BASE = f"{SHOP}media/images/"
CHOOSE = BASE
RESULTS = f"{BASE}results/"
CREATE = f"{BASE}create/"
DENIED = "vendor.media_denied"
UPLOADED = "vendor.media_uploaded"


def _chosen(pk: int) -> str:
    return f"{BASE}chosen/{pk}/"


def _collection(vendor: Vendor) -> Collection:
    root = Collection.get_first_root_node()
    return root.add_child(name=engine.vendor_collection_name(str(vendor.public_id)))


def _json(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.status_code
    return json.loads(response.content)  # type: ignore[no-any-return]


@pytest.fixture
def staff_member(vendor: Vendor) -> User:
    return VendorMemberFactory.create(vendor=vendor, role="staff").user


@pytest.fixture
def own(vendor: Vendor) -> Collection:
    return _collection(vendor)


@pytest.fixture
def foreign(other_vendor: Vendor) -> Collection:
    return _collection(other_vendor)


def _upload(name: str = "lake.png", data: bytes | None = None) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, png() if data is None else data)


def _post(client: Client, **extra: Any) -> Any:
    fields = {"image-chooser-upload-title": "Lake", "image-chooser-upload-file": _upload()}
    return client.post(CREATE, {**fields, **extra})


def test_the_chooser_lists_only_the_vendors_own_images(
    client: Client, owner: User, own: Collection, foreign: Collection
) -> None:
    mine = MediaImageFactory.create(collection=own, title="Mine alone")
    theirs = MediaImageFactory.create(collection=foreign, title="Theirs alone")
    platform = MediaImageFactory.create(title="Platform alone")
    verified_login(client, owner)
    data = _json(client.get(CHOOSE))
    assert data["step"] == "choose"
    assert "Mine alone" in data["html"]
    assert _chosen(mine.pk) in data["html"]
    for other in (theirs, platform):
        assert other.title not in data["html"]
        assert _chosen(other.pk) not in data["html"]
    assert "Mine alone" in client.get(RESULTS).content.decode()
    assert "Theirs alone" not in client.get(RESULTS).content.decode()


def test_the_chooser_offers_no_collection_choice(
    client: Client, owner: User, own: Collection, foreign: Collection
) -> None:
    verified_login(client, owner)
    html = _json(client.get(CHOOSE))["html"]
    assert "collection" not in re.sub(r"vendor-[0-9a-f-]+", "", html.lower().replace("_", ""))
    assert foreign.name not in html


def test_choosing_an_own_image_returns_it(client: Client, owner: User, own: Collection) -> None:
    image = MediaImageFactory.create(collection=own)
    verified_login(client, owner)
    data = _json(client.get(_chosen(image.pk)))
    assert data["step"] == "chosen"
    assert data["result"]["id"] == str(image.pk)


def test_the_format_step_serves_an_own_image(client: Client, owner: User, own: Collection) -> None:
    image = MediaImageFactory.create(collection=own)
    verified_login(client, owner)
    data = _json(client.get(f"{BASE}{image.pk}/select_format/"))
    assert data["step"] == "select_format"


@pytest.mark.parametrize("where", ["foreign", "platform", "missing"])
@pytest.mark.parametrize("route", ["chosen", "select_format"])
def test_another_vendors_image_id_is_a_404_and_is_audited(
    client: Client,
    owner: User,
    vendor: Vendor,
    own: Collection,
    foreign: Collection,
    where: str,
    route: str,
) -> None:
    pk = {
        "foreign": MediaImageFactory.create(collection=foreign).pk,
        "platform": MediaImageFactory.create().pk,
        "missing": 999_999,
    }[where]
    url = _chosen(pk) if route == "chosen" else f"{BASE}{pk}/select_format/"
    verified_login(client, owner)
    assert client.get(url).status_code == 404
    if route == "select_format":
        assert client.post(url, {}).status_code == 404
    entry = AuditLog.objects.filter(action=DENIED).first()
    assert entry is not None
    assert entry.actor_id == owner.pk
    assert entry.vendor_key == str(vendor.public_id)


@pytest.mark.parametrize("route", [CHOOSE, RESULTS])
def test_a_crafted_collection_filter_is_a_404_and_is_audited(
    client: Client, owner: User, own: Collection, foreign: Collection, route: str
) -> None:
    MediaImageFactory.create(collection=foreign, title="Theirs alone")
    verified_login(client, owner)
    assert client.get(route, {"collection_id": foreign.pk}).status_code == 404
    assert AuditLog.objects.filter(action=DENIED).count() == 1
    assert client.get(route, {"collection_id": own.pk}).status_code == 200


def test_an_owner_uploads_into_the_vendors_own_collection(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    data = _json(_post(client))
    assert data["step"] == "chosen"
    image = get_image_model().objects.get(pk=int(data["result"]["id"]))
    assert image.collection.name == engine.vendor_collection_name(str(vendor.public_id))
    assert image.collection.depth == 2
    assert image.uploaded_by_user_id == owner.pk
    assert image.licence is not None
    assert image.licence.code == "own-work"
    entry = AuditLog.objects.get(action=UPLOADED)
    assert entry.actor_id == owner.pk
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.after is not None
    assert entry.after["image"] == f"image:{image.pk}"


def test_an_upload_naming_another_collection_is_a_404_and_creates_nothing(
    client: Client, owner: User, own: Collection, foreign: Collection
) -> None:
    verified_login(client, owner)
    before = get_image_model().objects.count()
    response = _post(client, **{"image-chooser-upload-collection": foreign.pk})
    assert response.status_code == 404
    assert get_image_model().objects.count() == before
    assert AuditLog.objects.filter(action=DENIED).count() == 1
    assert not AuditLog.objects.filter(action=UPLOADED).exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("photo.jpg", HTML_PAGE),  # HTML named .jpg
        ("logo.png", SVG_WITH_SCRIPT),  # SVG with a script named .png
        ("logo.svg", SVG_WITH_SCRIPT),  # SVG is not allowed outside the logos collection
        ("empty.png", b""),
    ],
)
def test_a_disguised_or_unsafe_file_is_refused(
    client: Client, owner: User, own: Collection, name: str, content: bytes
) -> None:
    verified_login(client, owner)
    before = get_image_model().objects.count()
    response = _post(client, **{"image-chooser-upload-file": _upload(name, content)})
    assert response.status_code == 200
    assert json.loads(response.content)["step"] != "chosen"
    assert get_image_model().objects.count() == before
    assert not AuditLog.objects.filter(action=UPLOADED).exists()


def test_an_oversized_image_is_refused(
    client: Client, owner: User, own: Collection, settings: Any
) -> None:
    settings.WAGTAILIMAGES_MAX_UPLOAD_SIZE = 100
    verified_login(client, owner)
    before = get_image_model().objects.count()
    response = _post(client, **{"image-chooser-upload-file": _upload(data=png((300, 300)))})
    assert json.loads(response.content)["step"] != "chosen"
    assert get_image_model().objects.count() == before


def test_staff_and_other_users_get_404_and_anonymous_is_sent_to_sign_in(
    client: Client, staff_member: User, customer: User
) -> None:
    assert client.get(CHOOSE).status_code == 302
    for user in (staff_member, customer):
        verified_login(client, user)
        assert client.get(CHOOSE).status_code in (403, 404)
        assert client.post(CREATE, {}).status_code in (403, 404)
        client.logout()


def test_the_editor_points_at_the_manager_chooser_not_the_wagtail_admin(
    client: Client, owner: User
) -> None:
    verified_login(client, owner)
    page = client.get(f"{SHOP}profile/").content.decode()
    assert "/admin/images/chooser" not in page
    assert BASE in page


def test_the_manager_editor_has_no_link_or_chooser_feature_it_cannot_serve() -> None:
    assert "link" not in MANAGER_RICH_TEXT_FEATURES
    assert "image" in MANAGER_RICH_TEXT_FEATURES
