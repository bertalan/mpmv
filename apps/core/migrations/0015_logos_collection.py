"""THM.12: the "Logos and icons" collection, the only place SVG images are accepted."""

from typing import Any

from django.db import migrations

NAME = "Logos and icons"  # apps.core.svg.LOGO_COLLECTION_NAME at the time of this migration
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # treebeard MP_Node, steplen 4


def _step(number: int) -> str:
    digits = ""
    while number:
        number, remainder = divmod(number, len(ALPHABET))
        digits = ALPHABET[remainder] + digits
    return digits.rjust(4, "0")


def _number(step: str) -> int:
    return int(step, len(ALPHABET))


def create_collection(apps: Any, schema_editor: Any) -> None:
    Collection = apps.get_model("wagtailcore", "Collection")
    root = Collection.objects.filter(depth=1).order_by("path").first()
    if root is None or Collection.objects.filter(depth=2, name=NAME).exists():
        return
    children = Collection.objects.filter(depth=2, path__startswith=root.path).order_by("-path")
    last = children.first()
    number = _number(last.path[-4:]) + 1 if last is not None else 1
    Collection.objects.create(name=NAME, path=root.path + _step(number), depth=2, numchild=0)
    root.numchild = children.count()
    root.save(update_fields=["numchild"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_platform_owner_allowed_vendor_countries"),
        ("wagtailcore", "0025_collection_initial_data"),
    ]

    operations = [migrations.RunPython(create_collection, migrations.RunPython.noop)]
