"""D-34: the storefront description and policies become Wagtail rich text.

The columns stay `text`. The data step turns each plain-text value into `<p>` paragraphs
(blank lines split, single newlines become `<br/>`, the text is escaped) and cleans a value that
already has tags with the editor's allow-list. It is idempotent, and it keeps the translation
workflow's fingerprints: rows that were in step with the source text before stay in step.
"""

from django.db import migrations
from django.utils.module_loading import import_string
from wagtail.fields import RichTextField

from apps.core.translations import source_hash, source_language
from apps.vendors.engine import plain_to_html

RICH = ("description", "shipping_policy", "refund_policy", "cancellation_policy")
TEXTS = ("tagline", *RICH)


def convert(value: str) -> str:
    # By path: cms sits above vendors in the import contract.
    sanitise = import_string("apps.cms.engine.sanitise_rich_text")
    return str(sanitise(plain_to_html(value), None))


def to_rich_text(apps, schema_editor):  # type: ignore[no-untyped-def]
    VendorTranslation = apps.get_model("vendors", "VendorTranslation")
    source = source_language()
    vendor_ids = VendorTranslation.objects.values_list("vendor_id", flat=True).distinct()
    for vendor_id in vendor_ids:
        rows = list(VendorTranslation.objects.filter(vendor_id=vendor_id))
        old = next((r for r in rows if r.language == source), None)
        old_hash = source_hash({n: getattr(old, n) for n in TEXTS}) if old else None
        cleaned = {row.pk: {n: convert(getattr(row, n)) for n in RICH} for row in rows}
        for row in rows:
            VendorTranslation.objects.filter(pk=row.pk).update(**cleaned[row.pk])
        if old is None or old_hash is None:
            continue
        new_values = {"tagline": old.tagline, **cleaned[old.pk]}
        new_hash = source_hash(new_values)
        if new_hash == old_hash:
            continue
        for row in rows:
            if row.language == source or row.source_hash == old_hash:
                VendorTranslation.objects.filter(pk=row.pk).update(source_hash=new_hash)


class Migration(migrations.Migration):
    dependencies = [
        ("vendors", "0025_vendor_logo"),
    ]

    operations = [
        *(
            migrations.AlterField(
                model_name="vendortranslation", name=name, field=RichTextField(blank=True)
            )
            for name in RICH
        ),
        migrations.RunPython(to_rich_text, migrations.RunPython.noop),
    ]
