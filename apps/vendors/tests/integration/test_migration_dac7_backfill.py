"""VEN.23 (review of PR #137): the DAC7 migration backfills what existing vendors already hold.

The TIN issuing country follows the vendor's country when the stored TIN is valid there, and an
Italian entity's business registration number is its REA registration (office and number).
Nothing is guessed: an invalid TIN, an individual or a vendor without REA data stays empty.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.vendors.tests.factories import codice_fiscale, italian_vat

BEFORE = [("vendors", "0015_seed_dac7_seller_dataset")]
AFTER = [("vendors", "0016_backfill_dac7_seller_dataset")]


@pytest.mark.django_db(transaction=True)
def test_existing_vendors_get_the_tin_country_and_the_rea_registration() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    OldVendor = executor.loader.project_state(BEFORE).apps.get_model("vendors", "Vendor")
    vat = italian_vat(42)

    def create(slug: str, **values: object) -> None:
        OldVendor.objects.create(
            display_name=slug,
            slug=slug,
            legal_name=slug,
            contact_email=f"{slug}@example.com",
            **values,
        )

    create(
        "company",
        country="IT",
        legal_form="srl",
        tax_code=vat,
        rea_office="RM",
        rea_number="1234567",
    )
    create("person", country="IT", legal_form="sole_proprietorship", tax_code=codice_fiscale(7))
    create("bad-tin", country="IT", legal_form="srl", tax_code="12345678901")
    create("no-tin", country="DE", legal_form="other", tax_code="")
    create(
        "kept",
        country="IT",
        legal_form="srl",
        tax_code=vat,
        tin_country="SM",
        business_registration_number="SM-1",
        rea_office="RM",
        rea_number="7654321",
    )

    executor.loader.build_graph()
    executor.migrate(AFTER)
    NewVendor = executor.loader.project_state(AFTER).apps.get_model("vendors", "Vendor")
    rows = {v.slug: v for v in NewVendor.objects.all()}

    assert (rows["company"].tin_country, rows["company"].business_registration_number) == (
        "IT",
        "RM-1234567",
    )
    assert (rows["person"].tin_country, rows["person"].business_registration_number) == ("IT", "")
    assert rows["bad-tin"].tin_country == ""
    assert rows["no-tin"].tin_country == ""
    assert (rows["kept"].tin_country, rows["kept"].business_registration_number) == (
        "SM",
        "SM-1",
    )

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
