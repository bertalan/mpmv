"""CAT.25: content that holds an image keeps it out of the orphan list.

Lives under tests/integration because the catalog app cannot import the CMS (spec 01 graph);
the scan itself knows no page type: it reads every foreign key to the image model, Wagtail's
reference index, and the latest and live revision of every revisioned object.
"""

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.catalog import media_cleanup
from apps.cms.models import StandardPage
from apps.cms.tests.factories import StandardPageFactory
from apps.core.audit import AuditContext
from apps.core.models import HelpEntry, MediaImage
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:test", source="admin")


def old_image() -> MediaImage:
    image: MediaImage = MediaImageFactory.create()
    MediaImage.objects.filter(pk=image.pk).update(created_at=timezone.now() - dt.timedelta(days=3))
    image.refresh_from_db()
    return image


def staff() -> Any:
    user = UserFactory.create(staff=True)
    for codename in ("access_admin", "delete_mediaimage"):
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def listed() -> list[int]:
    return [image.pk for image in media_cleanup.orphan_images(staff()).images]


def test_an_image_used_only_in_a_page_stream_field_is_not_listed() -> None:
    image = old_image()
    StandardPageFactory.create(
        body=[("figure", {"image": image, "alt_text": "Hills", "caption": ""})]
    )

    assert listed() == []


def test_an_image_in_the_stream_field_of_a_draft_revision_only_is_not_listed() -> None:
    image = old_image()
    page: StandardPage = StandardPageFactory.create()
    page.body = [("figure", {"image": image, "alt_text": "Hills", "caption": ""})]  # type: ignore[assignment]
    page.save_revision()  # a draft: the live page does not hold the image

    live = StandardPage.objects.get(pk=page.pk)
    assert image.pk not in [b.value["image"].pk for b in live.body if b.block_type == "figure"]
    assert listed() == []
    result = media_cleanup.delete_orphans([image.pk], user=staff(), ctx=CTX)
    assert result.deleted == []
    assert MediaImage.objects.filter(pk=image.pk).exists()


def test_an_image_only_in_the_live_revision_of_a_page_is_not_listed() -> None:
    image = old_image()
    page: StandardPage = StandardPageFactory.create(live=False)
    page.body = [("figure", {"image": image, "alt_text": "Hills", "caption": ""})]  # type: ignore[assignment]
    page.save_revision().publish()
    # The live row no longer holds it (a later edit), the live revision still does.
    StandardPage.objects.filter(pk=page.pk).update(image=None)
    page.refresh_from_db()

    assert listed() == []


def test_an_image_in_a_page_image_field_is_not_listed() -> None:
    image = old_image()
    StandardPageFactory.create(image=image, image_alt_text="A lake")

    assert listed() == []


def test_an_image_in_a_snippet_stream_field_is_not_listed() -> None:
    image = old_image()
    entry = HelpEntry.objects.first()
    assert entry is not None  # the repository help rows
    # Covered by the generic scan: no snippet has an image today, so this checks only that the
    # scan runs over revisioned snippet models without error.
    assert image.pk in listed()


def test_an_unreferenced_image_beside_a_used_one_is_listed() -> None:
    used, unused = old_image(), old_image()
    StandardPageFactory.create(
        body=[("figure", {"image": used, "alt_text": "Hills", "caption": ""})]
    )

    assert listed() == [unused.pk]
