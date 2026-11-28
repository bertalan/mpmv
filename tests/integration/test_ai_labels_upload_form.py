"""CMP.8: the Wagtail image form treats `ai_origin` as optional.

A POST that leaves it out (every existing upload path, and a script) stores the image as `none`;
a POST that names it stores it; an edit that leaves it out keeps what was stored.
"""

import io

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image
from wagtail.models import Collection, GroupCollectionPermission

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog import media
from apps.core.models import AuditLog, ImageLicence, MediaImage
from apps.core.tests.factories import MediaImageFactory

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


# --- Who changes it (CMP.8, owner decision 2026-11-27): editors raise, staff lower, all audited


def _editor_client() -> Client:
    """A signed-in editor with image rights on the root collection, not platform staff."""
    user = UserFactory.create()
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    group = Group.objects.create(name=f"image-editors-{user.pk}")
    user.groups.add(group)
    for codename in ("add_image", "change_image"):
        GroupCollectionPermission.objects.create(
            group=group,
            collection=Collection.get_first_root_node(),
            permission=Permission.objects.get(
                content_type__app_label="wagtailimages", codename=codename
            ),
        )
    client = Client()
    verified_login(client, user)
    return client


def _entries() -> list[AuditLog]:
    return list(AuditLog.objects.filter(action=media.MEDIA_IMAGE_AI_ORIGIN_SET).order_by("pk"))


def _edit(client: Client, image: MediaImage, origin: str) -> str:
    fields = {k: v for k, v in _fields(image.title).items() if k != "file"}
    response = client.post(
        reverse("wagtailimages:edit", args=(image.pk,)), {**fields, "ai_origin": origin}
    )
    return response.content.decode()


def test_an_editor_raises_the_origin_and_it_is_audited() -> None:
    image = MediaImageFactory.create(title="raise")
    _edit(_editor_client(), image, "edited")
    image.refresh_from_db()
    assert image.ai_origin == "edited"
    (entry,) = _entries()
    assert (entry.before, entry.after) == ({"ai_origin": "none"}, {"ai_origin": "edited"})
    assert entry.actor_id is not None


@pytest.mark.parametrize(("stored", "wanted"), [("generated", "none"), ("generated", "edited")])
def test_an_editor_cannot_lower_the_origin_and_nothing_changes(stored: str, wanted: str) -> None:
    image = MediaImageFactory.create(title="lower", ai_origin=stored)
    html = _edit(_editor_client(), image, wanted)
    image.refresh_from_db()
    assert image.ai_origin == stored
    assert not _entries()
    assert "errorlist" in html or "error-message" in html  # the form shows the refusal


def test_staff_lower_the_origin_and_it_is_audited(editor: Client) -> None:
    image = MediaImageFactory.create(title="staff", ai_origin="generated")
    _edit(editor, image, "edited")
    image.refresh_from_db()
    assert image.ai_origin == "edited"
    (entry,) = _entries()
    assert (entry.before, entry.after) == ({"ai_origin": "generated"}, {"ai_origin": "edited"})


def test_a_new_upload_marked_generated_is_audited_from_none() -> None:
    _editor_client().post(
        reverse("wagtailimages:add"), {**_fields("fresh"), "ai_origin": "generated"}
    )
    image = MediaImage.objects.get(title="fresh")
    assert image.ai_origin == "generated"
    (entry,) = _entries()
    assert entry.target_key == str(image.pk)
    assert (entry.before, entry.after) == ({"ai_origin": "none"}, {"ai_origin": "generated"})


def test_an_upload_without_the_field_is_none_and_writes_no_audit_entry() -> None:
    _editor_client().post(reverse("wagtailimages:add"), _fields("quiet"))
    assert MediaImage.objects.get(title="quiet").ai_origin == "none"
    assert not _entries()


def test_an_edit_that_leaves_the_origin_out_or_unchanged_writes_no_entry(editor: Client) -> None:
    image = MediaImageFactory.create(title="same", ai_origin="edited")
    _edit(editor, image, "edited")
    fields = {k: v for k, v in _fields("same").items() if k != "file"}
    editor.post(reverse("wagtailimages:edit", args=(image.pk,)), fields)
    image.refresh_from_db()
    assert image.ai_origin == "edited"
    assert not _entries()
