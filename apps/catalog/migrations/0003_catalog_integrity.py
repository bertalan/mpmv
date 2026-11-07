"""Catalogue integrity rules (CAT.14, owner decisions Q-29, Q-31, Q-32, Q-33).

Besides the field and check changes, the database enforces what a check constraint cannot:

- a product never changes vendor (trigger on catalog_product; staff duplicate it instead);
- variants exist only on variable products (trigger on catalog_variant), and a variable product
  with variants cannot become simple (trigger on catalog_product);
- a required companion has the same vendor (composite foreign key on the (id, vendor) key).

The triggers raise check_violation, so Django reports them as IntegrityError.
"""

from django.db import migrations, models

import apps.core.fields

FORBID = """
CREATE OR REPLACE FUNCTION catalog_product_integrity() RETURNS trigger AS $$
BEGIN
    IF NEW.vendor_id IS DISTINCT FROM OLD.vendor_id THEN
        RAISE EXCEPTION 'catalog_product: a product never changes vendor (duplicate it instead)'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.kind <> 'variable' AND OLD.kind = 'variable'
            AND EXISTS (SELECT 1 FROM catalog_variant WHERE product_id = NEW.id) THEN
        RAISE EXCEPTION 'catalog_product: only a variable product has variants'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER catalog_product_integrity
    BEFORE UPDATE OF vendor_id, kind ON catalog_product
    FOR EACH ROW EXECUTE FUNCTION catalog_product_integrity();

CREATE OR REPLACE FUNCTION catalog_variant_needs_variable_product() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM catalog_product WHERE id = NEW.product_id AND kind = 'variable'
    ) THEN
        RAISE EXCEPTION 'catalog_variant: only a variable product can have variants'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER catalog_variant_needs_variable_product
    BEFORE INSERT OR UPDATE OF product_id ON catalog_variant
    FOR EACH ROW EXECUTE FUNCTION catalog_variant_needs_variable_product();

ALTER TABLE catalog_product ADD CONSTRAINT catalog_product_companion_same_vendor
    FOREIGN KEY (required_companion_id, vendor_id) REFERENCES catalog_product (id, vendor_id);
"""

ALLOW = """
ALTER TABLE catalog_product DROP CONSTRAINT IF EXISTS catalog_product_companion_same_vendor;
DROP TRIGGER IF EXISTS catalog_variant_needs_variable_product ON catalog_variant;
DROP FUNCTION IF EXISTS catalog_variant_needs_variable_product();
DROP TRIGGER IF EXISTS catalog_product_integrity ON catalog_product;
DROP FUNCTION IF EXISTS catalog_product_integrity();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_taxonomies"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="product",
            name="catalog_product_price_net_not_negative",
        ),
        migrations.RemoveConstraint(
            model_name="product",
            name="catalog_product_sale_price_below_price",
        ),
        migrations.AddField(
            model_name="product",
            name="mpn",
            field=models.CharField(
                blank=True,
                help_text="Manufacturer part number: with the brand it links the same article "
                "across vendors.",
                max_length=70,
                verbose_name="MPN",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="price_net",
            field=apps.core.fields.MoneyField(
                blank=True,
                decimal_places=2,
                help_text="EUR, VAT excluded. Required to sell; never 0.00 "
                "(leave it empty instead).",
                max_digits=12,
                null=True,
                verbose_name="net price",
            ),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("price_net__isnull", True), ("price_net__gt", 0), _connector="OR"
                ),
                name="catalog_product_price_net_positive",
                violation_error_message="The price must be above 0.00; leave it empty instead.",
            ),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("sales_mode", "buy"), _negated=True),
                    ("price_net__isnull", False),
                    _connector="OR",
                ),
                name="catalog_product_price_required_to_buy",
                violation_error_message="A product for sale needs a price.",
            ),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("sale_price_net__isnull", True),
                    models.Q(
                        ("price_net__isnull", False),
                        ("sale_price_net__gte", 0),
                        ("sale_price_net__lt", models.F("price_net")),
                    ),
                    _connector="OR",
                ),
                name="catalog_product_sale_price_below_price",
                violation_error_message="The sale price must be lower than the price.",
            ),
        ),
        migrations.RunSQL(FORBID, reverse_sql=ALLOW),
    ]
