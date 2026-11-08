"""CMS.8: the image licence allow list and the required image metadata (spec 10 §10.2)."""

import pytest
from django.core.exceptions import ValidationError
from wagtail.images.forms import get_image_form

from apps.core.audit import AuditContext
from apps.core.models import AuditLog, ImageLicence, MediaImage
from apps.core.services import images_needing_review, save_image, save_image_licence
from apps.core.tests.factories import ImageLicenceFactory, MediaImageFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:licences@example.com", source=AuditLog.Source.ADMIN)
SEEDED = {
    "own-work": (False, False, False),
    "vendor-supplied": (False, False, False),
    "written-permission": (True, True, True),
    "CC0-1.0": (False, True, False),
    "CC-BY-4.0": (True, True, False),
    "CC-BY-SA-4.0": (True, True, False),
}


def _licence(code: str) -> ImageLicence:
    return ImageLicence.objects.get(code=code)


def test_the_allow_list_is_seeded_per_the_spec() -> None:
    rows = {
        row.code: (row.attribution_required, row.source_required, row.permission_required)
        for row in ImageLicence.objects.all()
    }
    assert rows == SEEDED
    assert _licence("CC-BY-4.0").url == "https://creativecommons.org/licenses/by/4.0/"


def test_an_nc_licence_cannot_be_added_to_the_allow_list() -> None:
    licence = ImageLicenceFactory.build(code="CC-BY-NC-4.0", name="CC BY-NC 4.0")
    with pytest.raises(ValidationError) as excinfo:
        save_image_licence(licence, ctx=CTX)
    assert "code" in excinfo.value.message_dict
    assert not ImageLicence.objects.filter(code="CC-BY-NC-4.0").exists()
    assert not AuditLog.objects.filter(target_model="core.imagelicence").exists()


def test_adding_a_licence_is_audited() -> None:
    licence = ImageLicenceFactory.build(code="CC-BY-3.0", name="CC BY 3.0")
    changes = save_image_licence(licence, ctx=CTX)

    assert [c.op for c in changes.changes] == ["create"]
    entry = AuditLog.objects.get(action="core.image_licence.created")
    assert entry.target_key == str(licence.public_id)
    assert entry.after["code"] == "CC-BY-3.0"


def test_a_licence_dry_run_writes_nothing() -> None:
    licence = ImageLicenceFactory.build(code="CC-BY-2.0", name="CC BY 2.0")
    changes = save_image_licence(licence, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert not ImageLicence.objects.filter(code="CC-BY-2.0").exists()
    assert not AuditLog.objects.exists()


def test_a_service_upload_without_licence_or_author_is_refused() -> None:
    image = MediaImageFactory.build(licence=None, author="")
    with pytest.raises(ValidationError) as excinfo:
        save_image(image)
    assert {"licence", "author"} <= set(excinfo.value.message_dict)
    assert not MediaImage.objects.exists()


def test_a_service_upload_with_its_metadata_is_saved() -> None:
    image = MediaImageFactory.build(
        licence=_licence("CC-BY-4.0"), author="Ada", source_url="https://example.com/a"
    )
    image.collection = MediaImageFactory.create().collection
    save_image(image)
    assert MediaImage.objects.filter(pk=image.pk, author="Ada").exists()


def test_a_save_image_dry_run_validates_and_writes_nothing() -> None:
    image = MediaImageFactory.build(licence=_licence("own-work"), author="Operator")
    image.collection = MediaImageFactory.create().collection
    changes = save_image(image, dry_run=True)
    assert [c.op for c in changes.changes] == ["create"]
    assert image.pk is None


def test_the_admin_upload_form_refuses_an_image_without_licence_or_author() -> None:
    form_class = get_image_form(MediaImage)
    collection = MediaImageFactory.create().collection
    upload = MediaImageFactory.build().file
    form = form_class(
        {"title": "Lake", "collection": collection.pk, "tags": ""},
        {"file": upload},
        user=None,
    )
    assert not form.is_valid()
    assert {"licence", "author"} <= set(form.errors)


def test_the_admin_upload_form_has_the_metadata_fields() -> None:
    fields = set(get_image_form(MediaImage).base_fields)
    assert {"licence", "source_url", "author", "credit_text", "permission_ref"} <= fields


def test_migrated_images_without_metadata_are_listed_for_review() -> None:
    legacy = MediaImageFactory.create(licence=None, author="")
    no_author = MediaImageFactory.create(author="")
    classified = MediaImageFactory.create()

    review = set(images_needing_review())

    assert legacy in review
    assert no_author in review
    assert classified not in review


def test_a_cc_by_image_has_a_credit_and_a_cc0_image_none() -> None:
    by = MediaImageFactory.create(
        licence=_licence("CC-BY-4.0"), author="Ada", source_url="https://example.com/a"
    )
    cc0 = MediaImageFactory.create(licence=_licence("CC0-1.0"), author="Bob")
    assert by.credit is not None
    assert by.credit.text == "Ada"
    assert cc0.credit is None
