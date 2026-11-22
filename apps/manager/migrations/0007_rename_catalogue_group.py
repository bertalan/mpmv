"""I18N.23 follow-up: the role group "Catalog and moderation" becomes "Catalogue and moderation".

A rename of the group row only: its id, permissions and members stay. Idempotent in both
directions (a missing group, or one already renamed, is left alone). If a database that was
migrated from before this change already holds an empty group with the new name (an older
migration of the chain seeds from the current `roles.py`), the members and permissions of the
old group move into it and the old group goes.
"""

from django.db import migrations

OLD = "Catalog and moderation"
NEW = "Catalogue and moderation"


def _rename(apps, source: str, target: str) -> None:  # type: ignore[no-untyped-def]
    group_model = apps.get_model("auth", "Group")
    old = group_model.objects.filter(name=source).first()
    if old is None:
        return
    existing = group_model.objects.filter(name=target).exclude(pk=old.pk).first()
    if existing is None:
        old.name = target
        old.save(update_fields=["name"])
        return
    existing.permissions.add(*old.permissions.all())
    for user in old.user_set.all():
        user.groups.add(existing)
    old.delete()


def forwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    _rename(apps, OLD, NEW)


def backwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    _rename(apps, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [
        ("manager", "0006_catalog_review_notices"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
