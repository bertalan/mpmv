"""VEN.31 part 2 (D-34): the Manager document chooser and upload into the vendor's own collection.

Same rules as the image chooser: only the vendor's own collection is listed or addressable
(another vendor's document id is a 404 and audited), uploads go through the SEC.2 PDF checks, and
the stored document lives in `vendor-<id>`. Owner only.
"""

import json
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from wagtail.documents import get_document_model
from wagtail.models import Collection

from apps.accounts.models import User
from apps.accounts.tests.mfa import verified_login
from apps.core.models import AuditLog
from apps.manager.forms import MANAGER_RICH_TEXT_FEATURES
from apps.vendors import engine
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorMemberFactory
from tests.upload_corpus import HTML_PAGE, PDF, SVG_WITH_SCRIPT

from .conftest import SHOP

pytestmark = pytest.mark.django_db

BASE = f"{SHOP}media/documents/"
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


def _document(collection: Collection | None, title: str = "Terms") -> Any:
    doc = get_document_model()(
        title=title, collection=collection or Collection.get_first_root_node()
    )
    doc.file.save("terms.pdf", ContentFile(PDF), save=False)
    doc.save()
    return doc


def _json(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.status_code
    return json.loads(response.content)  # type: ignore[no-any-return]


@pytest.fixture
def own(vendor: Vendor) -> Collection:
    return _collection(vendor)


@pytest.fixture
def foreign(other_vendor: Vendor) -> Collection:
    return _collection(other_vendor)


def _post(client: Client, name: str = "terms.pdf", data: bytes | None = None, **extra: Any) -> Any:
    fields = {
        "document-chooser-upload-title": "Terms",
        "document-chooser-upload-file": SimpleUploadedFile(name, PDF if data is None else data),
    }
    return client.post(CREATE, {**fields, **extra})


def test_the_chooser_lists_only_the_vendors_own_documents(
    client: Client, owner: User, own: Collection, foreign: Collection
) -> None:
    mine = _document(own, "Mine alone")
    theirs = _document(foreign, "Theirs alone")
    platform = _document(None, "Platform alone")
    verified_login(client, owner)
    data = _json(client.get(CHOOSE))
    assert data["step"] == "choose"
    assert "Mine alone" in data["html"]
    assert _chosen(mine.pk) in data["html"]
    for other in (theirs, platform):
        assert other.title not in data["html"]
        assert _chosen(other.pk) not in data["html"]
    assert "Theirs alone" not in client.get(RESULTS).content.decode()
    assert foreign.name not in data["html"]


def test_choosing_an_own_document_returns_it(client: Client, owner: User, own: Collection) -> None:
    doc = _document(own)
    verified_login(client, owner)
    data = _json(client.get(_chosen(doc.pk)))
    assert data["step"] == "chosen"
    assert data["result"]["id"] == str(doc.pk)
    assert data["result"].get("edit_url") in (None, "")


@pytest.mark.parametrize("where", ["foreign", "platform", "missing"])
def test_another_vendors_document_id_is_a_404_and_is_audited(
    client: Client,
    owner: User,
    vendor: Vendor,
    own: Collection,
    foreign: Collection,
    where: str,
) -> None:
    pk = {
        "foreign": _document(foreign).pk,
        "platform": _document(None).pk,
        "missing": 999_999,
    }[where]
    verified_login(client, owner)
    assert client.get(_chosen(pk)).status_code == 404
    entry = AuditLog.objects.filter(action=DENIED).first()
    assert entry is not None
    assert entry.actor_id == owner.pk
    assert entry.vendor_key == str(vendor.public_id)


@pytest.mark.parametrize("route", [CHOOSE, RESULTS])
def test_a_crafted_collection_filter_is_a_404_and_is_audited(
    client: Client, owner: User, own: Collection, foreign: Collection, route: str
) -> None:
    verified_login(client, owner)
    assert client.get(route, {"collection_id": foreign.pk}).status_code == 404
    assert AuditLog.objects.filter(action=DENIED).count() == 1
    assert client.get(route, {"collection_id": own.pk}).status_code == 200


def test_an_owner_uploads_a_pdf_into_the_vendors_own_collection(
    client: Client, owner: User, vendor: Vendor
) -> None:
    verified_login(client, owner)
    data = _json(_post(client))
    assert data["step"] == "chosen"
    doc = get_document_model().objects.get(pk=int(data["result"]["id"]))
    assert doc.collection.name == engine.vendor_collection_name(str(vendor.public_id))
    assert doc.collection.depth == 2
    assert doc.uploaded_by_user_id == owner.pk
    assert doc.file.name.endswith(".pdf")
    entry = AuditLog.objects.get(action=UPLOADED)
    assert entry.actor_id == owner.pk
    assert entry.vendor_key == str(vendor.public_id)
    assert entry.after is not None
    assert entry.after["document"] == f"document:{doc.pk}"


def test_an_upload_naming_another_collection_is_a_404_and_creates_nothing(
    client: Client, owner: User, own: Collection, foreign: Collection
) -> None:
    verified_login(client, owner)
    before = get_document_model().objects.count()
    response = _post(client, **{"document-chooser-upload-collection": foreign.pk})
    assert response.status_code == 404
    assert get_document_model().objects.count() == before
    assert AuditLog.objects.filter(action=DENIED).count() == 1
    assert not AuditLog.objects.filter(action=UPLOADED).exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("guide.pdf", HTML_PAGE),  # HTML named .pdf
        ("guide.pdf", b"%PDF-1.4\n" + HTML_PAGE + PDF[9:]),  # PDF header, then HTML to sniff
        ("guide.html", HTML_PAGE),
        ("logo.svg", SVG_WITH_SCRIPT),
        ("setup.exe", b"MZ\x90\x00" + b"\x00" * 60),
        ("guide.pdf.exe", PDF),
        ("empty.pdf", b""),
    ],
)
def test_a_disguised_or_unsafe_file_is_refused(
    client: Client, owner: User, own: Collection, name: str, content: bytes
) -> None:
    verified_login(client, owner)
    before = get_document_model().objects.count()
    response = _post(client, name=name, data=content)
    assert response.status_code == 200
    assert json.loads(response.content)["step"] != "chosen"
    assert get_document_model().objects.count() == before
    assert not AuditLog.objects.filter(action=UPLOADED).exists()


def test_an_oversized_document_is_refused(
    client: Client, owner: User, own: Collection, settings: Any
) -> None:
    settings.WAGTAILDOCS_MAX_UPLOAD_SIZE = 100
    verified_login(client, owner)
    before = get_document_model().objects.count()
    response = _post(client, data=PDF + b" " * 200)
    assert json.loads(response.content)["step"] != "chosen"
    assert get_document_model().objects.count() == before


def test_the_upload_is_rate_limited(client: Client, owner: User, own: Collection) -> None:
    from apps.vendors.services import MEDIA_UPLOAD_LIMIT

    verified_login(client, owner)
    for _i in range(MEDIA_UPLOAD_LIMIT):
        assert _json(_post(client))["step"] == "chosen"
    assert json.loads(_post(client).content)["step"] != "chosen"
    assert AuditLog.objects.filter(action=UPLOADED).count() == MEDIA_UPLOAD_LIMIT


def test_staff_and_other_users_get_404_and_anonymous_is_sent_to_sign_in(
    client: Client, owner: User, vendor: Vendor, customer: User
) -> None:
    staff = VendorMemberFactory.create(vendor=vendor, role="staff").user
    assert client.get(CHOOSE).status_code == 302
    for user in (staff, customer):
        verified_login(client, user)
        assert client.get(CHOOSE).status_code in (403, 404)
        assert client.post(
            CREATE, {"document-chooser-upload-file": SimpleUploadedFile("a.pdf", PDF)}
        ).status_code in (403, 404)
        client.logout()
    assert not get_document_model().objects.exists()


def test_the_editor_has_no_document_link_until_private_storage(client: Client, owner: User) -> None:
    """Owner decision (ARC.15): no public-by-URL vendor PDFs, so no document link in the editor."""
    assert "document-link" not in MANAGER_RICH_TEXT_FEATURES
    verified_login(client, owner)
    page = client.get(f"{SHOP}profile/").content.decode()
    assert "/admin/documents/chooser" not in page
    assert BASE not in page


def test_the_dry_run_of_the_service_writes_nothing(
    owner: User, vendor: Vendor, own: Collection
) -> None:
    from apps.core.audit import AuditContext
    from apps.vendors.services import upload_storefront_document

    ctx = AuditContext(actor_label="user:owner@example.com", source="vendor")
    before = get_document_model().objects.count()
    upload_storefront_document(
        vendor,
        upload=SimpleUploadedFile("a.pdf", PDF),
        title="A",
        actor=owner,
        ctx=ctx,
        dry_run=True,
    )
    assert get_document_model().objects.count() == before
    assert not AuditLog.objects.filter(action=UPLOADED).exists()
