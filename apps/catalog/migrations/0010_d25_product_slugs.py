"""D-25 (CAT.5b): reslug existing products to `{title}`, without the language suffix.

The slug service does the work, so the rules (uniqueness per language, `-2` on a collision,
history rows, audit) live in one place. The replaced `{title}-{lang}` and legacy
`{title}-{lang}-{sku}` slugs stay in SlugHistory and answer a single 301 to the new slug. The
same service is available as `manage.py rebuild_product_slugs [--dry-run]`.
"""

from typing import Any

from django.db import migrations

from apps.core.audit import AuditContext
from apps.core.models import AuditLog


def reslug_products(apps: Any, schema_editor: Any, *, dry_run: bool = False) -> Any:
    # Imported here: the services use the current models, which match the schema at this point
    # only because the table is untouched by later migrations when there is data to reslug.
    from apps.catalog.services import rebuild_product_slugs

    ctx = AuditContext(
        actor_label="migration:catalog.0010_d25_product_slugs", source=AuditLog.Source.COMMAND
    )
    return rebuild_product_slugs(ctx=ctx, dry_run=dry_run)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0009_redirect"),
        ("core", "0017_operator_alert_emails"),
    ]

    operations = [
        migrations.RunPython(reslug_products, migrations.RunPython.noop),
    ]
