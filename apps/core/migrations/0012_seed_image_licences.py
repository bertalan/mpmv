"""CMS.8: the image licence allow list seeded per spec 10 §10.2. Later changes go through the
staff admin (audited)."""

import uuid
from typing import Any

from django.db import migrations

# Frozen copy at the time of this migration:
# (code, name, url, attribution_required, source_required, permission_required)
LICENCES = (
    ("own-work", "Own work", "", False, False, False),
    ("vendor-supplied", "Vendor supplied", "", False, False, False),
    ("written-permission", "Written permission", "", True, True, True),
    (
        "CC0-1.0",
        "CC0 1.0",
        "https://creativecommons.org/publicdomain/zero/1.0/",
        False,
        True,
        False,
    ),
    ("CC-BY-4.0", "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/", True, True, False),
    (
        "CC-BY-SA-4.0",
        "CC BY-SA 4.0",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        True,
        True,
        False,
    ),
)


def seed(apps: Any, schema_editor: Any) -> None:
    ImageLicence = apps.get_model("core", "ImageLicence")
    for code, name, url, attribution, source, permission in LICENCES:
        ImageLicence.objects.get_or_create(
            code=code,
            defaults={
                "public_id": uuid.uuid4(),
                "name": name,
                "url": url,
                "attribution_required": attribution,
                "source_required": source,
                "permission_required": permission,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0011_image_licence")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
