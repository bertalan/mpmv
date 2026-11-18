"""CAT.25: orphan `MediaImage` cleanup, service and staff review list.

Done when: an image used by no ProductImage is listed; deleting it writes an AuditLog row; an
image still in a gallery cannot be deleted. Also: a logo, a brand image or a platform logo is
never listed; a dry run deletes nothing; a person without the image delete permission is refused.
"""

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.accounts.tests.mfa import verified_login
from apps.catalog import media_cleanup
from apps.catalog.tests.factories import (
    BrandFactory,
    ProductImageFactory,
    brand_logo_image,
)
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, MediaImage, PlatformOperator
from apps.core.tests.factories import MediaImageFactory
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="staff:test", source="admin")
URL_NAME = "catalog_orphan_images"


def old_image(**fields: Any) -> MediaImage:
    """An image past the grace period."""
    image: MediaImage = MediaImageFactory.create(**fields)
    MediaImage.objects.filter(pk=image.pk).update(created_at=timezone.now() - dt.timedelta(days=3))
    image.refresh_from_db()
    return image


def editor(*codenames: str) -> Any:
    user = UserFactory.create(staff=True)
    for codename in codenames:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def image_admin() -> Any:
    """A person who may use the Wagtail admin and delete any image."""
    return editor("access_admin", "add_mediaimage", "change_mediaimage", "delete_mediaimage")


def listed(user: Any) -> list[int]:
    return [image.pk for image in media_cleanup.orphan_images(user).images]


def test_an_image_used_by_no_product_is_listed() -> None:
    orphan = old_image()
    used = old_image()
    ProductImageFactory.create(image=used)

    assert listed(image_admin()) == [orphan.pk]


def test_a_fresh_upload_is_not_listed_yet() -> None:
    MediaImageFactory.create()  # created now: an upload may still be waiting to be attached

    assert listed(image_admin()) == []


def test_a_vendor_logo_a_brand_logo_and_the_platform_logo_are_not_listed() -> None:
    vendor_logo, brand_logo, platform_logo = old_image(), old_image(), old_image()
    VendorFactory.create(logo=vendor_logo)
    BrandFactory.create(logo=brand_logo)
    operator = PlatformOperator.objects.order_by("-pk").first()
    assert operator is not None
    PlatformOperator.objects.filter(pk=operator.pk).update(logo=platform_logo)

    assert listed(image_admin()) == []


def test_a_brand_logo_from_the_platform_collection_is_not_listed() -> None:
    logo = brand_logo_image()
    MediaImage.objects.filter(pk=logo.pk).update(created_at=timezone.now() - dt.timedelta(days=3))
    BrandFactory.create(logo=logo)

    assert listed(image_admin()) == []


def test_deleting_an_orphan_writes_one_audit_row_per_image() -> None:
    first, second = old_image(), old_image()
    user = image_admin()

    result = media_cleanup.delete_orphans([first.pk, second.pk], user=user, ctx=CTX)

    assert sorted(result.deleted) == sorted([first.pk, second.pk])
    assert not MediaImage.objects.filter(pk__in=[first.pk, second.pk]).exists()
    rows = AuditLog.objects.filter(action=media_cleanup.ORPHAN_DELETED)
    assert sorted(rows.values_list("target_key", flat=True)) == sorted(
        [str(first.pk), str(second.pk)]
    )
    assert all(row.target_model == "core.mediaimage" for row in rows)


def test_an_image_still_in_a_gallery_cannot_be_deleted() -> None:
    image = old_image()
    ProductImageFactory.create(image=image)

    result = media_cleanup.delete_orphans([image.pk], user=image_admin(), ctx=CTX)

    assert result.deleted == []
    assert result.refused == {image.pk: ["catalog.productimage.image"]}
    assert MediaImage.objects.filter(pk=image.pk).exists()
    assert not AuditLog.objects.filter(action=media_cleanup.ORPHAN_DELETED).exists()


def test_a_reference_added_after_the_list_was_shown_stops_the_delete() -> None:
    image = old_image()
    assert listed(image_admin()) == [image.pk]
    ProductImageFactory.create(image=image)  # a vendor adds it to a gallery meanwhile

    result = media_cleanup.delete_orphans([image.pk], user=image_admin(), ctx=CTX)

    assert result.deleted == []
    assert MediaImage.objects.filter(pk=image.pk).exists()


def test_a_dry_run_deletes_nothing_and_audits_nothing() -> None:
    image = old_image()

    result = media_cleanup.delete_orphans([image.pk], user=image_admin(), ctx=CTX, dry_run=True)

    assert result.deleted == [image.pk]
    assert [c.op for c in result.changeset.changes] == ["delete"]
    assert MediaImage.objects.filter(pk=image.pk).exists()
    assert not AuditLog.objects.filter(action=media_cleanup.ORPHAN_DELETED).exists()


def test_a_fresh_image_is_not_deleted_even_when_asked() -> None:
    image = MediaImageFactory.create()

    result = media_cleanup.delete_orphans([image.pk], user=image_admin(), ctx=CTX)

    assert result.deleted == []
    assert MediaImage.objects.filter(pk=image.pk).exists()


def test_a_person_without_the_delete_permission_is_refused() -> None:
    image = old_image()
    user = editor("access_admin", "add_mediaimage", "change_mediaimage")

    with pytest.raises(PermissionDenied):
        media_cleanup.delete_orphans([image.pk], user=user, ctx=CTX)
    with pytest.raises(PermissionDenied):
        media_cleanup.orphan_images(user)
    assert MediaImage.objects.filter(pk=image.pk).exists()


def test_the_query_count_does_not_grow_with_the_number_of_orphans() -> None:
    old_image()
    user = image_admin()
    with CaptureQueriesContext(connection) as few:
        media_cleanup.orphan_images(user)
    for _ in range(6):
        old_image()
    with CaptureQueriesContext(connection) as many:
        result = media_cleanup.orphan_images(user)
        [(image.licence, image.collection) for image in result.images]

    assert len(result.images) == 7
    assert len(many) <= len(few) + 1


# --- the staff review list


def test_the_review_list_shows_orphans_and_a_non_staff_user_gets_403(client: Client) -> None:
    orphan = old_image(title="Lonely photo")
    verified_login(client, image_admin())

    response = client.get(reverse(URL_NAME))

    assert response.status_code == 200
    assert "Lonely photo" in response.content.decode()
    assert response.context["total"] == 1
    assert orphan.pk in [image.pk for image in response.context["images"]]


def test_a_customer_cannot_open_the_review_list(client: Client) -> None:
    verified_login(client, UserFactory.create())

    assert client.get(reverse(URL_NAME)).status_code in (302, 403, 404)


def test_an_editor_without_the_delete_permission_gets_403(client: Client) -> None:
    verified_login(client, editor("access_admin", "add_mediaimage", "change_mediaimage"))

    assert client.get(reverse(URL_NAME)).status_code == 403


def test_the_preview_button_deletes_nothing(client: Client) -> None:
    image = old_image()
    verified_login(client, image_admin())

    response = client.post(reverse(URL_NAME), {"image": [image.pk], "mode": "preview"})

    assert response.status_code == 200
    assert MediaImage.objects.filter(pk=image.pk).exists()
    assert not AuditLog.objects.filter(action=media_cleanup.ORPHAN_DELETED).exists()


def test_the_delete_button_removes_the_orphan_and_audits_it(client: Client) -> None:
    image = old_image()
    kept = old_image()
    ProductImageFactory.create(image=kept)
    verified_login(client, image_admin())

    response = client.post(
        reverse(URL_NAME), {"image": [image.pk, kept.pk], "mode": "delete"}, follow=True
    )

    assert response.status_code == 200
    assert not MediaImage.objects.filter(pk=image.pk).exists()
    assert MediaImage.objects.filter(pk=kept.pk).exists()
    row = AuditLog.objects.get(action=media_cleanup.ORPHAN_DELETED)
    assert row.target_key == str(image.pk)
    assert row.actor_id is not None


def test_a_post_without_the_delete_permission_changes_nothing(client: Client) -> None:
    image = old_image()
    verified_login(client, editor("access_admin", "add_mediaimage", "change_mediaimage"))

    response = client.post(reverse(URL_NAME), {"image": [image.pk], "mode": "delete"})

    assert response.status_code == 403
    assert MediaImage.objects.filter(pk=image.pk).exists()
