"""CAT.13: internal identity and the vendor SKU as a label (owner decisions Q-30 and Q-33).

- `sku` is kept raw; the upper-case normalisation of CAT.1 is dropped. `sku_key` (the match
  key, `engine.sku_match_key`) is filled for existing rows, then made unique per vendor.
- One namespace per vendor across products and variants: a trigger on both tables refuses a
  match key the other table already uses for the vendor. A per-vendor advisory lock serialises
  concurrent writers, and each check in the (volatile) function sees rows committed before the
  lock was granted, so two concurrent inserts cannot both pass (READ COMMITTED, the default).
- A trigger writes a `SkuAlias` row whenever a match key changes, whatever made the change.
- `staff_ref`: per-table sequences formatted by `catalog_staff_ref` ("P-000123", "V-000123").
"""

import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

import apps.catalog.fields
from apps.catalog.engine import sku_match_key


def fill_sku_keys(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Match keys for existing rows; stop with a list when a product and a variant of one
    vendor would share one (CAT.1 kept them in separate namespaces)."""
    product_model = apps.get_model("catalog", "Product")
    variant_model = apps.get_model("catalog", "Variant")
    for model in (product_model, variant_model):
        for row in model.objects.only("pk", "sku"):
            row.sku_key = sku_match_key(row.sku)
            row.save(update_fields=["sku_key"])
    clashes = [
        f"vendor {vendor_id}: {key!r}"
        for vendor_id, key in product_model.objects.values_list("vendor_id", "sku_key")
        if variant_model.objects.filter(vendor_id=vendor_id, sku_key=key).exists()
    ]
    if clashes:
        raise RuntimeError(
            "Products and variants share SKU match keys; rename them first: " + ", ".join(clashes)
        )


STAFF_REF_SQL = """
CREATE SEQUENCE catalog_product_staff_ref_seq;
CREATE SEQUENCE catalog_variant_staff_ref_seq;
CREATE FUNCTION catalog_staff_ref(prefix text, seq text) RETURNS text AS $$
DECLARE
    n text := nextval(seq::regclass)::text;
BEGIN
    RETURN prefix || lpad(n, greatest(6, length(n)), '0');
END;
$$ LANGUAGE plpgsql VOLATILE;
"""

STAFF_REF_REVERSE_SQL = """
DROP FUNCTION IF EXISTS catalog_staff_ref(text, text);
DROP SEQUENCE IF EXISTS catalog_variant_staff_ref_seq;
DROP SEQUENCE IF EXISTS catalog_product_staff_ref_seq;
"""

NAMESPACE_SQL = """
CREATE FUNCTION catalog_sku_namespace() RETURNS trigger AS $$
DECLARE
    taken boolean;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.sku_key = OLD.sku_key AND NEW.vendor_id = OLD.vendor_id THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('catalog_sku_namespace', NEW.vendor_id));
    IF TG_TABLE_NAME = 'catalog_product' THEN
        SELECT EXISTS (
            SELECT 1 FROM catalog_variant WHERE vendor_id = NEW.vendor_id AND sku_key = NEW.sku_key
        ) INTO taken;
    ELSE
        SELECT EXISTS (
            SELECT 1 FROM catalog_product WHERE vendor_id = NEW.vendor_id AND sku_key = NEW.sku_key
        ) INTO taken;
    END IF;
    IF taken THEN
        RAISE EXCEPTION 'vendor % already uses the SKU match key % for a product or variant',
            NEW.vendor_id, NEW.sku_key
            USING ERRCODE = 'unique_violation', CONSTRAINT = 'catalog_sku_namespace';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql VOLATILE;

CREATE TRIGGER catalog_product_sku_namespace
    BEFORE INSERT OR UPDATE OF sku_key, vendor_id ON catalog_product
    FOR EACH ROW EXECUTE FUNCTION catalog_sku_namespace();
CREATE TRIGGER catalog_variant_sku_namespace
    BEFORE INSERT OR UPDATE OF sku_key, vendor_id ON catalog_variant
    FOR EACH ROW EXECUTE FUNCTION catalog_sku_namespace();
"""

NAMESPACE_REVERSE_SQL = """
DROP TRIGGER IF EXISTS catalog_variant_sku_namespace ON catalog_variant;
DROP TRIGGER IF EXISTS catalog_product_sku_namespace ON catalog_product;
DROP FUNCTION IF EXISTS catalog_sku_namespace();
"""

ALIAS_SQL = """
CREATE FUNCTION catalog_sku_alias() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'catalog_product' THEN
        INSERT INTO catalog_skualias
            (public_id, created_at, updated_at, vendor_id, product_id, variant_id, sku, sku_key,
             replaced_at)
        VALUES
            (gen_random_uuid(), now(), now(), OLD.vendor_id, OLD.id, NULL, OLD.sku, OLD.sku_key,
             now());
    ELSE
        INSERT INTO catalog_skualias
            (public_id, created_at, updated_at, vendor_id, product_id, variant_id, sku, sku_key,
             replaced_at)
        VALUES
            (gen_random_uuid(), now(), now(), OLD.vendor_id, NULL, OLD.id, OLD.sku, OLD.sku_key,
             now());
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER catalog_product_sku_alias
    AFTER UPDATE OF sku_key ON catalog_product
    FOR EACH ROW WHEN (OLD.sku_key IS DISTINCT FROM NEW.sku_key)
    EXECUTE FUNCTION catalog_sku_alias();
CREATE TRIGGER catalog_variant_sku_alias
    AFTER UPDATE OF sku_key ON catalog_variant
    FOR EACH ROW WHEN (OLD.sku_key IS DISTINCT FROM NEW.sku_key)
    EXECUTE FUNCTION catalog_sku_alias();
"""

ALIAS_REVERSE_SQL = """
DROP TRIGGER IF EXISTS catalog_variant_sku_alias ON catalog_variant;
DROP TRIGGER IF EXISTS catalog_product_sku_alias ON catalog_product;
DROP FUNCTION IF EXISTS catalog_sku_alias();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_slug_history"),
        ("vendors", "0012_vendorfeatureflag"),
    ]

    operations = [
        migrations.CreateModel(
            name="SkuAlias",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "sku",
                    apps.catalog.fields.SkuField(
                        help_text="The SKU as it was entered.", max_length=64, verbose_name="SKU"
                    ),
                ),
                ("sku_key", models.CharField(max_length=64, verbose_name="SKU match key")),
                (
                    "replaced_at",
                    models.DateTimeField(db_default=django.db.models.functions.datetime.Now()),
                ),
            ],
            options={
                "verbose_name": "SKU alias",
                "verbose_name_plural": "SKU aliases",
                "ordering": ["vendor", "sku_key", "replaced_at"],
            },
        ),
        migrations.RemoveConstraint(
            model_name="product",
            name="catalog_product_vendor_sku_unique",
        ),
        migrations.RemoveConstraint(
            model_name="product",
            name="catalog_product_sku_normalized",
        ),
        migrations.RemoveConstraint(
            model_name="variant",
            name="catalog_variant_vendor_sku_unique",
        ),
        migrations.RemoveConstraint(
            model_name="variant",
            name="catalog_variant_sku_normalized",
        ),
        migrations.RunSQL(STAFF_REF_SQL, reverse_sql=STAFF_REF_REVERSE_SQL),
        migrations.AddField(
            model_name="product",
            name="sku_key",
            field=models.CharField(
                default="",
                editable=False,
                help_text="The SKU normalised for matching; unique per vendor.",
                max_length=64,
                verbose_name="SKU match key",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="product",
            name="staff_ref",
            field=models.CharField(
                db_default=models.Func(
                    models.Value("P-"),
                    models.Value("catalog_product_staff_ref_seq"),
                    function="catalog_staff_ref",
                    output_field=models.CharField(),
                ),
                editable=False,
                help_text="Internal reference for staff; never shown to vendors.",
                max_length=24,
                unique=True,
                verbose_name="staff reference",
            ),
        ),
        migrations.AddField(
            model_name="variant",
            name="sku_key",
            field=models.CharField(
                default="",
                editable=False,
                help_text="The SKU normalised for matching; unique per vendor.",
                max_length=64,
                verbose_name="SKU match key",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="variant",
            name="staff_ref",
            field=models.CharField(
                db_default=models.Func(
                    models.Value("V-"),
                    models.Value("catalog_variant_staff_ref_seq"),
                    function="catalog_staff_ref",
                    output_field=models.CharField(),
                ),
                editable=False,
                help_text="Internal reference for staff; never shown to vendors.",
                max_length=24,
                unique=True,
                verbose_name="staff reference",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="sku",
            field=apps.catalog.fields.SkuField(
                help_text="The vendor's label, kept as entered; unique per vendor ignoring case "
                "and spaces, across products and variants.",
                max_length=64,
                verbose_name="SKU",
            ),
        ),
        migrations.AlterField(
            model_name="variant",
            name="sku",
            field=apps.catalog.fields.SkuField(
                help_text="The vendor's label, kept as entered; unique per vendor ignoring case "
                "and spaces, across products and variants.",
                max_length=64,
                verbose_name="SKU",
            ),
        ),
        migrations.RunPython(fill_sku_keys, migrations.RunPython.noop),
        # Fire the deferred foreign key checks of those updates before altering the tables.
        migrations.RunSQL("SET CONSTRAINTS ALL IMMEDIATE", reverse_sql=migrations.RunSQL.noop),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                fields=("vendor", "sku_key"),
                name="catalog_product_vendor_sku_key_unique",
                violation_error_message="This vendor already has a product with this SKU.",
            ),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(
                condition=models.Q(("sku_key", ""), _negated=True),
                name="catalog_product_sku_key_present",
                violation_error_message="A SKU cannot be empty.",
            ),
        ),
        migrations.AddConstraint(
            model_name="variant",
            constraint=models.UniqueConstraint(
                fields=("vendor", "sku_key"),
                name="catalog_variant_vendor_sku_key_unique",
                violation_error_message="This vendor already has a variant with this SKU.",
            ),
        ),
        migrations.AddConstraint(
            model_name="variant",
            constraint=models.CheckConstraint(
                condition=models.Q(("sku_key", ""), _negated=True),
                name="catalog_variant_sku_key_present",
                violation_error_message="A SKU cannot be empty.",
            ),
        ),
        migrations.AddField(
            model_name="skualias",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sku_aliases",
                to="catalog.product",
            ),
        ),
        migrations.AddField(
            model_name="skualias",
            name="variant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="sku_aliases",
                to="catalog.variant",
            ),
        ),
        migrations.AddField(
            model_name="skualias",
            name="vendor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sku_aliases",
                to="vendors.vendor",
            ),
        ),
        migrations.AddIndex(
            model_name="skualias",
            index=models.Index(fields=["vendor", "sku_key"], name="catalog_skualias_vendor_key"),
        ),
        migrations.AddConstraint(
            model_name="skualias",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("product__isnull", False), ("variant__isnull", True)),
                    models.Q(("product__isnull", True), ("variant__isnull", False)),
                    _connector="OR",
                ),
                name="catalog_skualias_product_xor_variant",
                violation_error_message="An alias belongs to a product or to a variant.",
            ),
        ),
        migrations.RunSQL(NAMESPACE_SQL, reverse_sql=NAMESPACE_REVERSE_SQL),
        migrations.RunSQL(ALIAS_SQL, reverse_sql=ALIAS_REVERSE_SQL),
    ]
