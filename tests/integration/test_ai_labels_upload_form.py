"""CMP.8: the Wagtail image form treats `ai_origin` as optional.

A POST that leaves it out (every existing upload path, and a script) stores the image as `none`;
a POST that names it stores it; an edit that leaves it out keeps what was stored.
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image
from wagtail.models import Collection

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.core.models import ImageLicence, MediaImage

pytestmark = pytest.mark.django_db


def _png() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, format="PNG")
    return SimpleUploadedFile("p.png", buffer.getvalue())


def _fields(title: str) -> dict[str, object]:
    return {
        "title": title,
        "collection": Collection.get_first_root_node().pk,
        "licence": ImageLicence.objects.get(code="own-work").pk,
        "author": "Test author",
        "file": _png(),
    }


@pytest.fixture
def editor(client: Client) -> Client:
    verified_login(client, UserFactory.create(superuser=True))
    return client


def test_an_upload_without_ai_origin_is_stored_as_none(editor: Client) -> None:
    editor.post(reverse("wagtailimages:add"), _fields("plain"))
    assert MediaImage.objects.get(title="plain").ai_origin == "none"


def test_an_upload_naming_the_origin_stores_it_and_an_edit_without_it_keeps_it(
    editor: Client,
) -> None:
    editor.post(reverse("wagtailimages:add"), {**_fields("made"), "ai_origin": "generated"})
    image = MediaImage.objects.get(title="made")
    assert image.ai_origin == "generated"
    fields = {k: v for k, v in _fields("made again").items() if k != "file"}
    editor.post(reverse("wagtailimages:edit", args=(image.pk,)), fields)
    image.refresh_from_db()
    assert image.title == "made again"
    assert image.ai_origin == "generated"
