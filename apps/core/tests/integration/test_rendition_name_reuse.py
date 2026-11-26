"""OPS.17: image file names can repeat, so `/media/images/` keeps a 24-hour browser cache.

Uploads keep a cleaned copy of the uploaded file name (SEC.2), and a rendition is named after
its original. Delete an image and upload a new one under the same name, and the new image gets
the very same original and rendition paths. A longer `max-age` would then show visitors the old
picture for that long. If file names become unique one day, this test fails: then raise the
Caddyfile `max-age` for renditions (OPS.17) and change this test.
"""

import pytest

from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_new_image_with_a_deleted_images_name_reuses_its_paths() -> None:
    first = MediaImageFactory.create(file__filename="pot.png", file__color="red")
    old = (first.file.name, first.get_rendition("width-32").url)
    first.delete()
    second = MediaImageFactory.create(file__filename="pot.png", file__color="blue")
    assert (second.file.name, second.get_rendition("width-32").url) == old
