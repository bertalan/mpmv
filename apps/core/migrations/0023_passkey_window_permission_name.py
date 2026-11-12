"""CORE.25 wording (D-32): the `core.elevate` permission is described with the passkey window.

The codename is a stored identifier (roles hold it) and keeps its name; only the label changes.
"""

from django.db import migrations

OLD = "May switch a session to operational mode or do a sensitive action once"
NEW = "May open a passkey window or do a sensitive action once"


def _rename(apps, old: str, new: str) -> None:  # type: ignore[no-untyped-def]
    permission = apps.get_model("auth", "Permission")
    permission.objects.filter(content_type__app_label="core", codename="elevate", name=old).update(
        name=new
    )


def forwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    _rename(apps, OLD, NEW)


def backwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    _rename(apps, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0022_field_help_texts"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="platformoperator",
            options={
                "ordering": ["-valid_from"],
                "permissions": [
                    (
                        "platform_owner",
                        "Platform owner: owner-only settings, such as the allowed vendor countries",
                    ),
                    ("elevate", "May open a passkey window or do a sensitive action once"),
                ],
            },
        ),
        migrations.RunPython(forwards, backwards),
    ]
