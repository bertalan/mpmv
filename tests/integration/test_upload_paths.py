"""SEC.2: the malicious-upload corpus against every upload path of the site.

Done when (spec 12 §12.6): "Tests with malicious files". Each path takes every case of
`tests/upload_corpus.py` and either refuses it (nothing stored, not even a pending upload) or
stores it safely (`assert_safe_image` / `assert_safe_document`: a name the app made, a re-encoded
original without metadata or payload, a format that matches its extension, capped pixels).

The upload paths on main, found from every `FileField` of every installed model
(`test_every_stored_file_field_is_declared` fails when one is added without being declared here):

- images (`core.MediaImage.file`): the Wagtail admin's single upload, multiple upload (with the
  metadata completed afterwards), file replacement and the chooser's upload (page blocks, event
  listing images), and `services.save_image` (CMS.8: imports and the vendor area);
- documents (`wagtaildocs.Document.file`): the Wagtail admin's single, multiple and chooser
  uploads.

Media are served by Caddy only, as inert files with `nosniff` (deploy/caddy/Caddyfile, tested in
tests/unit/test_quadlets.py); `assert_safe_image` checks that every stored name is one it serves.
"""

import io
import itertools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import Client
from django.urls import reverse
from PIL import Image
from wagtail.documents.models import Document
from wagtail.models import Collection, UploadedFile
from wagtail.utils.file import hash_filelike

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.api.v1.api import api
from apps.core.models import ImageLicence, MediaImage
from apps.core.services import save_image
from apps.core.tests.factories import MediaImageFactory
from tests.upload_corpus import (
    Case,
    assert_safe_document,
    assert_safe_image,
    document_cases,
    image_cases,
    name_cases,
)

pytestmark = pytest.mark.django_db

_titles = itertools.count()

# Every stored file field and what writes to it. A new FileField or ImageField must be added
# here with the path that protects it, and its upload paths to the matrix below.
STORED_FILE_FIELDS = {
    "core.mediaimage.file": "SanitisedImageField: checked in clean, re-encoded when stored",
    "core.mediaimagerendition.file": "written by Wagtail from the re-encoded original",
    "wagtaildocs.document.file": "DocumentForm (WAGTAILDOCS_DOCUMENT_FORM_BASE): PDF by content",
    "wagtailcore.uploadedfile.file": (
        "Wagtail's pending multiple upload: written only after the form's file checks, kept "
        "under wagtail_uploads/ (Caddy answers 404), turned into a MediaImage or Document"
    ),
    "wagtailimages.image.file": "unused: WAGTAILIMAGES_IMAGE_MODEL is core.MediaImage",
    "wagtailimages.rendition.file": "unused: renditions of the unused wagtailimages.Image",
    "wagtailusers.userprofile.avatar": "no upload: the avatar panel is withdrawn (CMS.1)",
}


@dataclass(frozen=True)
class Stored:
    name: str
    data: bytes


def _read(field_file: Any) -> Stored:
    with field_file.open("rb") as handle:
        return Stored(name=field_file.name, data=handle.read())


@pytest.fixture
def editor(client: Client) -> Client:
    verified_login(client, UserFactory.create(superuser=True))
    return client


@pytest.fixture
def root() -> Collection:
    return Collection.get_first_root_node()


def _licence() -> ImageLicence:
    return ImageLicence.objects.get(code="own-work")


def _image_fields(title: str, root: Collection, prefix: str = "") -> dict[str, Any]:
    return {
        f"{prefix}title": title,
        f"{prefix}collection": root.pk,
        f"{prefix}licence": _licence().pk,
        f"{prefix}author": "Test author",
    }


def _upload(case: Case) -> SimpleUploadedFile:
    return SimpleUploadedFile(case.filename, case.data)


# --- Image paths ----------------------------------------------------------------------------
# Each takes the client, the root collection and a case, and returns the stored file or None.

ImagePath = Callable[[Client, Collection, Case], Stored | None]


def _new_image(title: str) -> Stored | None:
    image = MediaImage.objects.filter(title=title).first()
    return None if image is None else _stored_image(image)


def _stored_image(image: MediaImage) -> Stored:
    stored = _read(image.file)
    assert image.file_size == len(stored.data)  # size and hash are those of the stored file
    assert image.file_hash == hash_filelike(io.BytesIO(stored.data))
    with Image.open(io.BytesIO(stored.data)) as pixels:
        assert (image.width, image.height) == pixels.size
    return stored


def admin_single_upload(client: Client, root: Collection, case: Case) -> Stored | None:
    title = f"single {next(_titles)}"
    data = {**_image_fields(title, root), "file": _upload(case)}
    response = client.post(reverse("wagtailimages:add"), data)
    assert response.status_code in {200, 302}
    return _new_image(title)


def admin_multiple_upload(client: Client, root: Collection, case: Case) -> Stored | None:
    """Wagtail's drag-and-drop uploader: the file first, then the licence and author."""
    title = f"multiple {next(_titles)}"
    pending_before = UploadedFile.objects.count()
    response = client.post(
        reverse("wagtailimages:add_multiple"), {"files[]": _upload(case), "collection": root.pk}
    )
    assert response.status_code == 200
    result = response.json()
    if not result["success"]:
        assert UploadedFile.objects.count() == pending_before  # nothing kept
        return None
    upload_id = result["uploaded_file_id"]  # pending: the image needs its licence and author
    prefix = f"uploaded-image-{upload_id}-"
    response = client.post(
        reverse("wagtailimages:create_multiple_from_uploaded_image", args=(upload_id,)),
        _image_fields(title, root, prefix),
    )
    assert response.status_code == 200
    assert response.json()["success"], response.json()
    return _new_image(title)


def admin_replace_file(client: Client, root: Collection, case: Case) -> Stored | None:
    image = MediaImageFactory.create(collection=root, licence=_licence())
    before = image.file.name
    data = {**_image_fields(image.title, root), "file": _upload(case)}
    response = client.post(reverse("wagtailimages:edit", args=(image.pk,)), data)
    assert response.status_code in {200, 302}
    image.refresh_from_db()
    return None if image.file.name == before else _stored_image(image)


def admin_chooser_upload(client: Client, root: Collection, case: Case) -> Stored | None:
    """The upload tab of the image chooser (page blocks, event listing images)."""
    title = f"chooser {next(_titles)}"
    prefix = "image-chooser-upload-"
    data = {**_image_fields(title, root, prefix), f"{prefix}file": _upload(case)}
    response = client.post(reverse("wagtailimages_chooser:create"), data)
    assert response.status_code == 200
    return _new_image(title)


def service_save_image(client: Client, root: Collection, case: Case) -> Stored | None:
    """`services.save_image` (CMS.8): imports and the vendor area."""
    title = f"service {next(_titles)}"
    image = MediaImage(
        title=title, file=_upload(case), collection=root, licence=_licence(), author="Test author"
    )
    errors: dict[str, list[str]] = {}
    try:
        save_image(image)
    except ValidationError as refusal:
        errors = refusal.message_dict
    assert set(errors) <= {"file"}, errors  # refused for its file, never for anything else
    return _new_image(title)


IMAGE_PATHS: dict[str, ImagePath] = {
    "admin-single": admin_single_upload,
    "admin-multiple": admin_multiple_upload,
    "admin-replace": admin_replace_file,
    "admin-chooser": admin_chooser_upload,
    "service-save-image": service_save_image,
}


def _matrix(paths: dict[str, Any], cases: list[Case]) -> list[object]:
    params: list[object] = []
    for (path_id, path), case in itertools.product(paths.items(), cases):
        marks = []
        if case.xfail:
            marks.append(pytest.mark.xfail(reason=case.xfail, strict=True))
        if case.skip:
            marks.append(pytest.mark.skip(reason=case.skip))
        params.append(pytest.param(path, case, id=f"{path_id}:{case.id}", marks=marks))
    return params


@pytest.mark.parametrize(("path", "case"), _matrix(IMAGE_PATHS, image_cases() + name_cases()))
def test_every_image_upload_path_refuses_or_safely_stores_the_corpus(
    editor: Client, root: Collection, path: ImagePath, case: Case
) -> None:
    images_before = MediaImage.objects.count()
    stored = path(editor, root, case)
    if case.verdict == "refuse":
        assert stored is None, f"{case.why}: stored as {stored and stored.name}"
        assert MediaImage.objects.count() <= images_before + (path is admin_replace_file)
        return
    assert stored is not None, case.why
    assert_safe_image(stored.name, stored.data, case)


@pytest.mark.xfail(reason="needs #151 (THM.12: sanitised SVG logos)", strict=False)
def test_a_sanitised_svg_is_accepted_in_the_logos_collection_only(
    editor: Client, root: Collection
) -> None:
    logos = Collection.objects.get(name="Logos and icons")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect/></svg>'
    data = {**_image_fields("logo", logos), "file": SimpleUploadedFile("logo.svg", svg)}
    editor.post(reverse("wagtailimages:add"), data)
    assert MediaImage.objects.filter(title="logo").exists()


@pytest.mark.xfail(
    reason="Finding: Wagtail keeps a pending multiple upload as uploaded (wagtail_uploads/, not "
    "served) until the editor completes or deletes it; nothing purges abandoned ones",
    strict=True,
)
def test_a_pending_multiple_upload_keeps_no_location(editor: Client, root: Collection) -> None:
    case = next(c for c in image_cases() if c.id == "jpeg-exif-gps")
    response = editor.post(
        reverse("wagtailimages:add_multiple"), {"files[]": _upload(case), "collection": root.pk}
    )
    pending = UploadedFile.objects.get(pk=response.json()["uploaded_file_id"])
    assert all(payload not in _read(pending.file).data for payload in case.payloads)


# --- Document paths -------------------------------------------------------------------------

DocumentPath = Callable[[Client, Collection, Case], Stored | None]


def _new_document(title: str) -> Stored | None:
    document = Document.objects.filter(title=title).first()
    return None if document is None else _read(document.file)


def admin_document_upload(client: Client, root: Collection, case: Case) -> Stored | None:
    title = f"document {next(_titles)}"
    data = {"title": title, "collection": root.pk, "file": _upload(case)}
    response = client.post(reverse("wagtaildocs:add"), data)
    assert response.status_code in {200, 302}
    return _new_document(title)


def admin_document_multiple_upload(client: Client, root: Collection, case: Case) -> Stored | None:
    before = set(Document.objects.values_list("pk", flat=True))
    pending_before = UploadedFile.objects.count()
    response = client.post(
        reverse("wagtaildocs:add_multiple"), {"files[]": _upload(case), "collection": root.pk}
    )
    assert response.status_code == 200
    assert UploadedFile.objects.count() == pending_before
    if not response.json()["success"]:
        return None
    (document,) = Document.objects.exclude(pk__in=before)
    return _read(document.file)


def admin_document_chooser_upload(client: Client, root: Collection, case: Case) -> Stored | None:
    title = f"chooser document {next(_titles)}"
    prefix = "document-chooser-upload-"
    data = {
        f"{prefix}title": title,
        f"{prefix}collection": root.pk,
        f"{prefix}file": _upload(case),
    }
    response = client.post(reverse("wagtaildocs_chooser:create"), data)
    assert response.status_code == 200
    return _new_document(title)


DOCUMENT_PATHS: dict[str, DocumentPath] = {
    "docs-single": admin_document_upload,
    "docs-multiple": admin_document_multiple_upload,
    "docs-chooser": admin_document_chooser_upload,
}


@pytest.mark.parametrize(("path", "case"), _matrix(DOCUMENT_PATHS, document_cases()))
def test_every_document_upload_path_refuses_or_safely_stores_the_corpus(
    editor: Client, root: Collection, path: DocumentPath, case: Case
) -> None:
    stored = path(editor, root, case)
    if case.verdict == "refuse":
        assert stored is None, f"{case.why}: stored as {stored and stored.name}"
        return
    assert stored is not None, case.why
    assert_safe_document(stored.name, stored.data, case)


# --- Coverage of the paths ------------------------------------------------------------------


def test_every_stored_file_field_is_declared() -> None:
    """A new FileField or ImageField fails here until its upload paths are covered above."""
    found = {
        f"{model._meta.label_lower}.{field.name}"
        for model in apps.get_models()
        for field in model._meta.get_fields()
        if isinstance(field, models.FileField)
    }
    assert found == set(STORED_FILE_FIELDS)


def test_the_api_takes_no_file_upload() -> None:
    """API v1 has no multipart operation; one added must join the matrix above."""
    schema = api.get_openapi_schema()
    bodies = [
        operation.get("requestBody", {}).get("content", {})
        for item in schema["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict)
    ]
    assert not [body for body in bodies if "multipart/form-data" in body]
